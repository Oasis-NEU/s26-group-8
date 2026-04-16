"""
Backend API server for NEU Professor Ratings.
No pandas/numpy — queries CockroachDB directly per request.

Install deps:  pip install flask flask-cors flask-limiter psycopg2-binary pyjwt requests python-dotenv gunicorn
Run:           python server.py
"""

import os, re, unicodedata, json, hashlib, random
import html as _html
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor
from functools import lru_cache
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, redirect, make_response
from flask_cors import CORS
from flask_compress import Compress
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import jwt as pyjwt
import requests as http_requests
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from threading import Lock, Thread, Event
import time

load_dotenv()


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────
def term_sort_key(title: str) -> int:
    """Returns a numeric sort key where higher = more recent term.
    Order within a year: Fall(7) > Fall A(6) > Full Summer(5) > Summer 2(4) > Summer 1(3) > Spring(2) > Spring A(1)
    """
    if not title:
        return 0
    lower = title.lower()
    # Try word-bounded year first, then 4-digit prefix of 6-digit code (e.g. "202510")
    m = re.search(r'\b(20\d{2})\b', lower) or re.search(r'(20\d{2})\d{2}', lower)
    if not m:
        return 0
    year = int(m.group(1))
    if re.search(r'\bfall\b', lower):
        sub = 6 if re.search(r'\bfall\s+a\b', lower) else 7
    elif re.search(r'\bfull\s+summer\b', lower):
        sub = 5
    elif re.search(r'\bsummer\b', lower):
        if re.search(r'\bsummer\s+2\b', lower):
            sub = 4
        elif re.search(r'\bsummer\s+1\b', lower):
            sub = 3
        else:
            sub = 4
    elif re.search(r'\bspring\b', lower):
        sub = 1 if re.search(r'\bspring\s+a\b', lower) else 2
    else:
        sub = 0
    return year * 10 + sub


def normalize_name(name):
    s = str(name).strip().lower()
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'\s+', ' ', s).strip()
    return s


# Maps RMP name variants → canonical trace names (normalized).
# Must stay in sync with ALIAS_MAP in precompute.py.
ALIAS_MAP = {
    "laney strange": "elena strange",
    "ben tasker": "benjamin tasker",
    "alberto de la torre": "alberto de la torre duran",
    "justin wang": "hsiao-an wang",
    "sakib miazi": "md nazmus sakib miazi",
    "nazmus miazi": "md nazmus sakib miazi",
    "alex depaoli": "alexander depaoli",
    "denisee spencer": "denise spencer",
    "chris bruell": "christopher bruell",
    "hande ondemir": "hande musdal ondemir",
    "francis frank georges": "francis georges",
    "isabel campos": "isabel sobral campos",
    "mary sue potts-santone": "mary-susan potts-santone",
    "ronald c. zullo": "ronald zullo",
    "steve granelli": "steven granelli",
    "william (bill) goldman": "william goldman",
    "virgiliu pavlu": "virgil pavlu",
    "zhiyuan (katherine) zhang": "zhiyuan zhang",
    "katherine zhang": "zhiyuan zhang",
    "bill goldman": "william goldman",
    "aarti sathyanaran": "aarti sathyanarayana",
    "akash murty": "akash murthy",
    "ali chaleshtari": "ali shirzadeh chaleshtari",
    "sriram rajagopalan": "sriramasundarar rajagopalan",
    "mauricio codesso": "mauricio mello codesso",
    "magda cooney": "magdalena cooney",
    "john lowery": "john lowrey",
    "iesha karasik": "ieshia karasik",
    "ifa khan": "iffat khan",
    "h. david sherman": "h sherman",
    "ganish krisnamoorthy": "ganesh krishnamoorthy",
    "farena sultan": "fareena sultan",
    "cathy merlo": "catherine merlo",
    "ye yin": "yi yin",
    "silvio amir": "silvio amir alves moreira",
    "olin shivers": "olin shivers iii",
    "rush sanghrajka": "rushit sanghrajka",
    "john alexis gomez": "john alexis guerra gomez",
    "ji yong shin": "ji-yong shin",
    "ghita amor tijani": "ghita amor-tijani",
    "bob lupi": "robert lupi",
    "hany sadaka": "hanai sadaka",
    "mary- susan potts": "mary-susan potts-santone",
    "xiaotao (kelvin) liu": "xiaotao liu",
    "kelvin liu": "xiaotao liu",
}

# Build a word-level mapping so partial/typeahead queries also resolve.
# e.g. typing "virgiliu" (an RMP-only spelling) still finds "virgil".
_WORD_ALIAS = {}
for _from, _to in ALIAS_MAP.items():
    _from_words = set(_from.split())
    _to_words = set(_to.split())
    for w in _from_words - _to_words:
        # Strip parens/punctuation so "(katherine)" becomes "katherine",
        # "c." becomes "c", matching what normalize_name produces.
        clean = re.sub(r'[^a-z0-9\-]', '', w)
        if clean:
            _WORD_ALIAS[clean] = _to_words - _from_words


def resolve_alias(q):
    """Return the canonical (trace) query if q matches an alias, else q."""
    return ALIAS_MAP.get(q, q)


def sanitize(text: str) -> str:
    return _html.unescape(str(text))


def friendly_count(n: int) -> str:
    if n < 100:
        return str(n)
    rounded = (n // 100) * 100
    return f"{rounded:,}+"


def _name_to_slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


# ──────────────────────────────────────────────
#  App setup
# ──────────────────────────────────────────────
app = Flask(__name__)
app.config["COMPRESS_MIMETYPES"] = ["application/json", "text/html"]
app.config["COMPRESS_MIN_SIZE"] = 256
Compress(app)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
CORS(app, supports_credentials=True, origins=[FRONTEND_URL])
limiter = Limiter(get_remote_address, app=app, default_limits=["120 per minute", "20 per second"])


BLOCKED_USER_AGENTS = [
    "python-requests", "python-httpx", "python-urllib", "aiohttp",
    "scrapy", "curl", "wget", "go-http-client", "java/", "okhttp",
    "httpie", "postmanruntime", "node-fetch", "undici",
    "gptbot", "ccbot", "claudebot", "bytespider", "google-extended",
    "ahrefsbot", "semrushbot", "dotbot", "mj12bot", "petalbot",
    "barkrowler", "dataforseobot",
]

# ── Auto-ban: temporarily block IPs that repeatedly hit the rate limit ──
_banned_ips = {}        # ip -> ban_expiry timestamp
_ban_lock = Lock()
_rate_limit_strikes = {}  # ip -> [timestamps of 429 hits]
BAN_STRIKE_THRESHOLD = 5  # strikes within the window to trigger a ban
BAN_STRIKE_WINDOW = 60    # seconds to count strikes
BAN_DURATION = 600        # 10 min ban

ALLOWED_ORIGINS = {
    FRONTEND_URL,
    "http://localhost:5173",
    "http://localhost:3000",
}


@app.before_request
def block_banned_ips():
    ip = get_remote_address()
    with _ban_lock:
        expiry = _banned_ips.get(ip)
        if expiry and time.time() < expiry:
            return jsonify({"error": "Temporarily blocked"}), 403
        elif expiry:
            del _banned_ips[ip]


@app.before_request
def block_bots():
    ua = (request.headers.get("User-Agent") or "").lower()
    if ua and any(bot in ua for bot in BLOCKED_USER_AGENTS):
        return jsonify({"error": "Forbidden"}), 403


@app.before_request
def check_origin():
    """Block API requests that don't originate from the frontend."""
    if request.path.startswith("/api/auth/google"):
        return  # allow OAuth redirects
    if request.method == "OPTIONS":
        return  # allow CORS preflight
    origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
    if origin and not any(origin.startswith(o) for o in ALLOWED_ORIGINS):
        return jsonify({"error": "Forbidden"}), 403


@app.after_request
def track_rate_limit_bans(response):
    """Auto-ban IPs that repeatedly trigger rate limits."""
    if response.status_code == 429:
        ip = get_remote_address()
        now = time.time()
        with _ban_lock:
            strikes = _rate_limit_strikes.setdefault(ip, [])
            strikes.append(now)
            # prune old strikes outside the window
            _rate_limit_strikes[ip] = [t for t in strikes if now - t < BAN_STRIKE_WINDOW]
            if len(_rate_limit_strikes[ip]) >= BAN_STRIKE_THRESHOLD:
                _banned_ips[ip] = now + BAN_DURATION
                del _rate_limit_strikes[ip]
    return response


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ──────────────────────────────────────────────
#  Database connection pool
# ──────────────────────────────────────────────
CRDB_DATABASE_URL = os.getenv("CRDB_DATABASE_URL")
if not CRDB_DATABASE_URL:
    raise RuntimeError("CRDB_DATABASE_URL environment variable is required")

_pool = None

def _get_pool():
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(2, 10, CRDB_DATABASE_URL, sslmode="require",
                                       connect_timeout=5,
                                       keepalives=1, keepalives_idle=30,
                                       keepalives_interval=10, keepalives_count=3)
    return _pool

# ──────────────────────────────────────────────
#  Simple in-memory cache (TTL-based)
# ──────────────────────────────────────────────
_cache = {}
_cache_lock = Lock()
CACHE_TTL = 3600      # 1 hour
CACHE_MAX_SIZE = 5000

_feedback_lock = Lock()
_feedback_count = 0
_feedback_date = None   # "YYYY-MM-DD" UTC, resets counter each day
FEEDBACK_DAILY_LIMIT = 300



def cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < CACHE_TTL:
            return entry["data"]
        return None


def cache_set(key, data):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": time.time()}
        if len(_cache) > CACHE_MAX_SIZE:
            cutoff = time.time() - CACHE_TTL
            expired = [k for k, v in _cache.items() if v["ts"] < cutoff]
            for k in expired:
                del _cache[k]


# ──────────────────────────────────────────────
#  Daily memory reset at 09:00 UTC
# ──────────────────────────────────────────────
_shutdown_event = Event()
_reset_thread_started = False

def _seconds_until_next_9utc():
    now = datetime.now(timezone.utc)
    target = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()

def _daily_cache_reset():
    while not _shutdown_event.is_set():
        wait = _seconds_until_next_9utc()
        if _shutdown_event.wait(timeout=wait):
            break
        try:
            with _cache_lock:
                _cache.clear()
            print(f"[{datetime.now(timezone.utc).isoformat()}] Daily cache reset complete",
                  flush=True)
        except Exception as e:
            print(f"[{datetime.now(timezone.utc).isoformat()}] Cache reset error: {e}",
                  flush=True)

def _start_reset_thread():
    global _reset_thread_started
    if not _reset_thread_started:
        _reset_thread_started = True
        t = Thread(target=_daily_cache_reset, daemon=True)
        t.start()


def _acquire_fresh_conn():
    key = id(g._get_current_object() if hasattr(g, '_get_current_object') else g)
    g.db_key = key
    g.db = _get_pool().getconn(key=key)
    return g.db


def _discard_db_conn():
    """Return the current request's connection to the pool and mark it closed."""
    db = g.pop('db', None)
    key = g.pop('db_key', None)
    if db is not None:
        try:
            _get_pool().putconn(db, key=key, close=True)
        except Exception:
            try:
                db.close()
            except Exception:
                pass


def get_db():
    if 'db' not in g:
        return _acquire_fresh_conn()
    conn = g.db
    if conn.closed:
        _discard_db_conn()
        return _acquire_fresh_conn()
    return conn


@app.before_request
def _ensure_reset_thread():
    _start_reset_thread()


@app.teardown_appcontext
def return_db(exc):
    db = g.pop('db', None)
    key = g.pop('db_key', None)
    if db is not None:
        try:
            _get_pool().putconn(db, key=key)
        except KeyError:
            try:
                db.close()
            except Exception:
                pass


def query(sql, params=None):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params or ())
        return cur.fetchall()
    except (psycopg2.InterfaceError, psycopg2.OperationalError):
        # Connection was stale — discard it and retry once with a fresh one
        _discard_db_conn()
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params or ())
        return cur.fetchall()


def query_one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None


# ──────────────────────────────────────────────
#  Google OAuth config
# ──────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable is required")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


# ──────────────────────────────────────────────
#  Search helper
# ──────────────────────────────────────────────
def _professor_search(q, limit=5):
    """Search professors with tiered relevance ranking."""
    # Resolve full-query alias first (e.g. "virgiliu pavlu" → "virgil pavlu")
    q_resolved = resolve_alias(q)

    words = q_resolved.split()
    if not words:
        return []

    # Expand individual words through the word-level alias map so partial
    # typeahead queries like "virgiliu" also match "virgil pavlu".
    expanded_words = set(words)
    for w in words:
        if w in _WORD_ALIAS:
            expanded_words.update(_WORD_ALIAS[w])

    # Build WHERE: each *original* word must match, OR its alias expansion must
    conditions = []
    params = []
    for word in words:
        alt_words = _WORD_ALIAS.get(word)
        if alt_words:
            group = [word] + list(alt_words)
            conditions.append("(" + " OR ".join("name_key LIKE %s" for _ in group) + ")")
            params.extend(f"%{w}%" for w in group)
        else:
            conditions.append("name_key LIKE %s")
            params.append(f"%{word}%")

    where = " AND ".join(conditions)
    rows = query(
        f"SELECT slug, name, name_key, department, avg_rating, total_reviews "
        f"FROM professors_catalog WHERE {where} "
        f"ORDER BY total_reviews DESC LIMIT 100",
        params
    )

    # Rank in Python for proper tiered relevance (use resolved query)
    q_rank = q_resolved
    words_rank = q_resolved.split()

    def rank_match(row):
        nk = row['name_key']
        parts = nk.split()

        # Tier 1: q matches a whole name part exactly
        if q_rank in parts:
            return 1
        # Tier 2: q matches the start of any name part
        if any(p.startswith(q_rank) for p in parts):
            return 2
        # Tier 3: q is a substring of the full name
        if q_rank in nk:
            return 3
        # Tier 4: multi-word: each word prefixes a name part
        if len(words_rank) >= 2 and all(any(p.startswith(w) for p in parts) for w in words_rank):
            return 4
        # Tier 5: multi-word: each word is substring of any name part
        if len(words_rank) >= 2 and all(any(w in p for p in parts) for w in words_rank):
            return 5
        return 6

    ranked = sorted(rows, key=lambda r: (rank_match(r), -(r['total_reviews'] or 0)))
    return ranked[:limit]


# ──────────────────────────────────────────────
#  API Routes
# ──────────────────────────────────────────────
@app.route("/api/stats")
def stats():
    cached = cache_get("stats")
    if cached:
        resp = jsonify(cached)
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp
    rows = query("SELECT key, value FROM stats_cache")
    stat_map = {r['key']: r['value'] for r in rows}
    result = [
        {"label": "Professors", "value": friendly_count(stat_map.get('professors', 0))},
        {"label": "Courses", "value": friendly_count(stat_map.get('courses', 0))},
        {"label": "Comments", "value": friendly_count(stat_map.get('comments', 0))},
        {"label": "Departments", "value": friendly_count(stat_map.get('departments', 0))},
    ]
    cache_set("stats", result)
    resp = jsonify(result)
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/api/colleges")
def colleges():
    cached = cache_get("colleges")
    if cached:
        return jsonify(cached)
    rows = query("""
        SELECT college, COUNT(*) as cnt FROM professors_catalog
        WHERE avg_rating IS NOT NULL
        GROUP BY college HAVING COUNT(*) >= 5
        ORDER BY college
    """)
    result = [r['college'] for r in rows if r['college'] != 'Other']
    cache_set("colleges", result)
    return jsonify(result)


NO_MIN_COLLEGES = {"Law", "Professional Studies"}


@app.route("/api/goat-professors")
def goat_professors():
    college = request.args.get("college", "Khoury")
    limit = min(int(request.args.get("limit", "10")), 50)
    min_reviews = int(request.args.get("min_reviews", "100"))

    cache_key = f"goat:{college}:{limit}:{min_reviews}"
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    if college in NO_MIN_COLLEGES:
        rows = query("""
            SELECT * FROM professors_catalog
            WHERE college = %s AND total_reviews >= 5
            ORDER BY avg_rating DESC NULLS LAST, total_reviews DESC
            LIMIT %s
        """, (college, limit))
    else:
        rows = query("""
            SELECT * FROM professors_catalog
            WHERE college = %s AND total_reviews >= %s
            ORDER BY avg_rating DESC NULLS LAST, total_reviews DESC
            LIMIT %s
        """, (college, min_reviews, limit))

    # Batch-count RMP + TRACE comments
    comment_counts = {}
    if rows:
        name_keys = [row["name_key"] for row in rows]
        placeholders = ",".join(["%s"] * len(name_keys))
        combined_counts = query(
            f"SELECT name_key, SUM(cnt) as cnt FROM ("
            f"  SELECT name_key, COUNT(*) as cnt FROM rmp_reviews "
            f"  WHERE name_key IN ({placeholders}) AND comment IS NOT NULL AND comment != '' "
            f"  GROUP BY name_key"
            f"  UNION ALL "
            f"  SELECT tc2.name_key, COUNT(*) as cnt "
            f"  FROM trace_comments tc "
            f"  JOIN trace_courses tc2 ON tc.tc_course_id = tc2.course_id "
            f"    AND tc.tc_instructor_id = tc2.instructor_id "
            f"    AND tc.tc_term_id = tc2.term_id "
            f"  WHERE tc2.name_key IN ({placeholders}) "
            f"  AND tc.comment IS NOT NULL AND tc.comment != '' "
            f"  GROUP BY tc2.name_key"
            f") sub GROUP BY name_key",
            name_keys + name_keys
        )
        for r in combined_counts:
            comment_counts[r["name_key"]] = int(r["cnt"])

    result = []
    for row in rows:
        result.append({
            "name": row["name"],
            "dept": row["department"],
            "rmpRating": round(row["rmp_rating"], 2) if row["rmp_rating"] else None,
            "traceRating": round(row["trace_rating"], 2) if row["trace_rating"] else None,
            "avgRating": round(row["avg_rating"], 2) if row["avg_rating"] else None,
            "totalComments": comment_counts.get(row["name_key"], 0),
        })
    cache_set(cache_key, result)
    return jsonify(result)


@app.route("/api/random-professor")
def random_professor():
    count_row = query_one("SELECT COUNT(*) as cnt FROM professors_catalog WHERE num_ratings >= 3")
    total = count_row["cnt"] if count_row else 0
    if total == 0:
        return jsonify({"error": "No professors found"}), 404
    offset = random.randint(0, total - 1)
    row = query_one("""
        SELECT * FROM professors_catalog
        WHERE num_ratings >= 3
        LIMIT 1 OFFSET %s
    """, (offset,))
    if not row:
        return jsonify({"error": "No professors found"}), 404
    return jsonify({
        "name": row["name"],
        "dept": row["department"],
        "college": row["college"],
    })


def _format_course_code(raw: str) -> str:
    return re.sub(r"\s+", "", str(raw).upper())


def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@app.route("/api/search")
def search():
    q = normalize_name(request.args.get("q", ""))
    search_type = request.args.get("type", "Professor")
    limit = min(int(request.args.get("limit", "5")), 20)

    if len(q) < 2:
        return jsonify([])

    if search_type == "Professor":
        matches = _professor_search(q, limit)
        results = []
        for r in matches:
            results.append({
                "type": "professor",
                "name": r["name"],
                "dept": r["department"],
                "rating": round(r["avg_rating"], 2) if r["avg_rating"] and r["avg_rating"] > 0 else None,
                "slug": r["slug"],
            })
        return jsonify(results)

    else:
        # Course search
        rows = query("""
            SELECT code, name, department FROM course_catalog
            WHERE search_text LIKE %s
            ORDER BY
                CASE WHEN lower(code) LIKE %s THEN 0 ELSE 1 END,
                code
            LIMIT %s
        """, (f"%{q}%", f"{q}%", limit))

        results = []
        for r in rows:
            results.append({
                "type": "course",
                "code": r["code"],
                "name": r["name"],
                "dept": r["department"],
            })
        return jsonify(results)


# ──────────────────────────────────────────────
#  Radar chart metric definitions (professor profile)
# ──────────────────────────────────────────────
_RADAR_METRICS = [
    {"metric": "Teaching", "patterns": [
        ["overall rating of teaching", "overall rating", "overall effectiveness", "what is your overall rating"],
        ["clearly communicated", "clear communication", "clearly"],
    ]},
    {"metric": "Organization", "patterns": [
        ["online course materials were organized", "online course materials"],
        ["syllabus was accurate", "syllabus"],
        ["used class time effectively", "effective time"],
    ]},
    {"metric": "Rigor", "patterns": [
        ["intellectually challenging", "this course was intellectually", "challenging"],
        ["learned a lot", "i learned a lot"],
    ]},
    {"metric": "Grading", "patterns": [
        ["fairly evaluated", "fair evaluation", "fair grades"],
        ["sufficient feedback", "provided sufficient feedback", "feedback"],
    ]},
    {"metric": "Accessibility", "patterns": [
        ["available to assist students outside", "outside assist"],
        ["respectful and inclusive", "facilitated a respectful", "respect"],
    ]},
]


def _get_radar_metric_value(scores, pattern_groups):
    values = []
    for group in pattern_groups:
        match = next((s for s in scores if any(p in s["question"].lower() for p in group)), None)
        if match and match["mean"] > 0:
            values.append(match["mean"])
    return round(sum(values) / len(values), 2) if values else None


# ──────────────────────────────────────────────
#  Professor profile page
# ──────────────────────────────────────────────
@app.route("/api/professors/<slug>")
def professor_profile(slug):
    is_authed = False
    token = _get_auth_token()
    if token:
        try:
            pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            is_authed = True
        except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
            pass

    cache_key = f"prof:{slug}:{'a' if is_authed else 'u'}"
    cached = cache_get(cache_key)
    if cached:
        resp = jsonify(cached)
        resp.headers["Cache-Control"] = "private, max-age=3600" if is_authed else "public, max-age=3600"
        resp.headers["Vary"] = "Authorization"
        return resp

    # Look up professor from catalog
    prof = query_one("SELECT * FROM professors_catalog WHERE slug = %s", (slug,))

    if not prof:
        # Try resolving slug to name_key
        name_key = slug.strip().lower().replace("-", " ")
        prof = query_one("SELECT * FROM professors_catalog WHERE name_key = %s", (name_key,))

    if not prof:
        return jsonify({"error": "Professor not found"}), 404

    name_key = prof["name_key"]

    profile = {
        "name": prof["name"],
        "department": prof["department"],
        "rmpRating": round(prof["rmp_rating"], 2) if prof["rmp_rating"] else None,
        "traceRating": round(prof["trace_rating"], 2) if prof["trace_rating"] else None,
        "avgRating": round(prof["avg_rating"], 2) if prof["avg_rating"] else 0.0,
        "wouldTakeAgainPct": round(prof["would_take_again_pct"], 1) if prof["would_take_again_pct"] else None,
        "difficulty": round(prof["difficulty"], 2) if prof["difficulty"] else None,
        "totalRatings": prof["total_reviews"],
        "professorUrl": prof["professor_url"],
        "imageUrl": prof["image_url"],
        "hoursPerWeek": round(prof["avg_hours"], 1) if prof["avg_hours"] else None,
    }

    # ── TRACE courses + scores ──
    # Authenticated: full scores. Unauthenticated: metadata + precomputed traceAvgDifficulty only.
    trace_course_list = []
    trace_course_rows = query("""
        SELECT course_id, term_id, term_title, department_name, display_name,
               section, enrollment, instructor_id
        FROM trace_courses WHERE name_key = %s
        ORDER BY term_id DESC
    """, (name_key,))

    if is_authed:
        if trace_course_rows:
            keys = tuple((int(c["course_id"]), int(c["instructor_id"]), int(c["term_id"] or 0)) for c in trace_course_rows)

            # ── SQL-aggregated per-course hours & challenge (replaces scores_by_key iteration) ──
            # Note: CockroachDB requires explicit float casts for mixed-type arithmetic
            per_course_agg = query("""
                SELECT course_id, instructor_id, term_id,
                       SUM(CASE WHEN LOWER(question) LIKE '%%hours%%' THEN
                           CASE WHEN COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0) > 0
                                THEN (1.0*COALESCE(count_1,0)::float+3.5*COALESCE(count_2,0)::float+6.0*COALESCE(count_3,0)::float+9.0*COALESCE(count_4,0)::float+12.0*COALESCE(count_5,0)::float)
                                ELSE COALESCE(mean::float, 0) END
                           ELSE 0 END)::float AS hours_sum,
                       SUM(CASE WHEN LOWER(question) LIKE '%%hours%%' THEN
                           CASE WHEN COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0) > 0
                                THEN (COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0))::float
                                ELSE CASE WHEN mean IS NOT NULL THEN 1.0 ELSE 0 END END
                           ELSE 0 END)::float AS hours_weight,
                       SUM(CASE WHEN LOWER(question) LIKE '%%challeng%%' THEN
                           CASE WHEN COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0) > 0
                                THEN (1.0*COALESCE(count_1,0)::float+2.0*COALESCE(count_2,0)::float+3.0*COALESCE(count_3,0)::float+4.0*COALESCE(count_4,0)::float+5.0*COALESCE(count_5,0)::float)
                                ELSE COALESCE(mean::float, 0) END
                           ELSE 0 END)::float AS challeng_sum,
                       SUM(CASE WHEN LOWER(question) LIKE '%%challeng%%' THEN
                           CASE WHEN COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0) > 0
                                THEN (COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0))::float
                                ELSE CASE WHEN mean IS NOT NULL THEN 1.0 ELSE 0 END END
                           ELSE 0 END)::float AS challeng_weight,
                       SUM(CASE WHEN LOWER(question) LIKE '%%overall%%' THEN
                           CASE WHEN COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0) > 0
                                THEN (1.0*COALESCE(count_1,0)::float+2.0*COALESCE(count_2,0)::float+3.0*COALESCE(count_3,0)::float+4.0*COALESCE(count_4,0)::float+5.0*COALESCE(count_5,0)::float)
                                ELSE COALESCE(mean::float, 0) END
                           ELSE 0 END)::float AS overall_sum,
                       SUM(CASE WHEN LOWER(question) LIKE '%%overall%%' THEN
                           CASE WHEN COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0) > 0
                                THEN (COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0))::float
                                ELSE CASE WHEN mean IS NOT NULL THEN 1.0 ELSE 0 END END
                           ELSE 0 END)::float AS overall_weight
                FROM trace_scores
                WHERE (course_id, instructor_id, term_id) IN %s
                GROUP BY course_id, instructor_id, term_id
            """, (keys,))
            pca_lookup = {(int(r["course_id"]), int(r["instructor_id"]), int(r["term_id"] or 0)): r for r in per_course_agg}

            # ── SQL-aggregated rating distribution by course code (overall questions only) ──
            rating_dist_rows = query("""
                SELECT tc.display_name,
                       SUM(COALESCE(ts.count_1,0)) AS c1,
                       SUM(COALESCE(ts.count_2,0)) AS c2,
                       SUM(COALESCE(ts.count_3,0)) AS c3,
                       SUM(COALESCE(ts.count_4,0)) AS c4,
                       SUM(COALESCE(ts.count_5,0)) AS c5,
                       SUM(COALESCE(ts.completed,0)) AS completed
                FROM trace_scores ts
                JOIN trace_courses tc
                  ON ts.course_id=tc.course_id AND ts.instructor_id=tc.instructor_id AND ts.term_id=tc.term_id
                WHERE tc.name_key = %s AND LOWER(ts.question) LIKE '%%overall%%'
                GROUP BY tc.display_name
            """, (name_key,))
            rating_dist_by_course = {}
            for r in rating_dist_rows:
                dn = str(r["display_name"] or "")
                m = re.match(r"^([A-Z]+\d+)", dn)
                course_code = (m.group(1) if m else dn.split(":")[0].split(" ")[0]).upper()
                if course_code not in rating_dist_by_course:
                    rating_dist_by_course[course_code] = {"count1": 0, "count2": 0, "count3": 0, "count4": 0, "count5": 0, "completed": 0}
                rating_dist_by_course[course_code]["count1"] += int(r["c1"] or 0)
                rating_dist_by_course[course_code]["count2"] += int(r["c2"] or 0)
                rating_dist_by_course[course_code]["count3"] += int(r["c3"] or 0)
                rating_dist_by_course[course_code]["count4"] += int(r["c4"] or 0)
                rating_dist_by_course[course_code]["count5"] += int(r["c5"] or 0)
                rating_dist_by_course[course_code]["completed"] += int(r["completed"] or 0)
        else:
            pca_lookup = {}
            rating_dist_by_course = {}

        # ── Build trace_course_list from SQL-aggregated data ──
        challeng_sum, challeng_weight = 0.0, 0
        for c in trace_course_rows:
            cid = int(c["course_id"])
            iid = int(c["instructor_id"])
            tid = int(c["term_id"]) if c["term_id"] else 0
            agg = pca_lookup.get((cid, iid, tid))
            if agg:
                hw = float(agg["hours_weight"] or 0)
                hs = float(agg["hours_sum"] or 0)
                cw = float(agg["challeng_weight"] or 0)
                cs = float(agg["challeng_sum"] or 0)
                ow = float(agg["overall_weight"] or 0)
                os_ = float(agg["overall_sum"] or 0)
                challeng_sum += cs
                challeng_weight += cw
            else:
                hw, hs, cw, cs, ow, os_ = 0, 0, 0, 0, 0, 0
            course_hours = round(hs / hw, 1) if hw > 0 else None
            course_overall = round(os_ / ow, 2) if ow > 0 else None
            trace_course_list.append({
                "courseId": cid,
                "termId": tid,
                "termTitle": str(c["term_title"] or ""),
                "departmentName": str(c["department_name"] or ""),
                "displayName": str(c["display_name"] or ""),
                "hoursPerWeek": course_hours,
                "challengeWeightedSum": cs if cw > 0 else None,
                "challengeResponses": cw if cw > 0 else None,
                "overallRating": course_overall,
            })

        trace_avg_difficulty = round(challeng_sum / challeng_weight, 2) if challeng_weight > 0 else None
        profile["traceRatingCounts"] = rating_dist_by_course

        # ── Precompute radar data for the most recent term with scores ──
        radar_data = None
        radar_term_title = None
        seen_tids: list[int] = []
        seen_tid_set: set[int] = set()
        for c in trace_course_rows:
            tid = int(c["term_id"]) if c["term_id"] else 0
            if tid not in seen_tid_set:
                seen_tids.append(tid)
                seen_tid_set.add(tid)

        for tid in seen_tids:
            term_keys = tuple(
                (int(tc["course_id"]), int(tc["instructor_id"]), tid)
                for tc in trace_course_rows
                if (int(tc["term_id"]) if tc["term_id"] else 0) == tid
            )
            if not term_keys:
                continue
            # SQL-aggregated radar data per question for this term
            # Note: mean*w/w*w simplifies to weighted_sum; CockroachDB needs explicit float casts
            radar_rows = query("""
                SELECT question,
                       SUM(CASE WHEN COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0) > 0
                            THEN (1.0*COALESCE(count_1,0)::float+2.0*COALESCE(count_2,0)::float+3.0*COALESCE(count_3,0)::float+4.0*COALESCE(count_4,0)::float+5.0*COALESCE(count_5,0)::float)
                            ELSE COALESCE(mean::float, 0) END)::float AS prof_sum,
                       SUM(CASE WHEN COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0) > 0
                            THEN (COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0))::float
                            ELSE 1.0 END)::float AS prof_w,
                       SUM(CASE WHEN dept_mean IS NOT NULL THEN
                            dept_mean::float * CASE WHEN COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0) > 0
                                             THEN (COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0))::float
                                             ELSE 1.0 END
                            ELSE 0 END)::float AS dept_sum,
                       SUM(CASE WHEN dept_mean IS NOT NULL THEN
                            CASE WHEN COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0) > 0
                                 THEN (COALESCE(count_1,0)+COALESCE(count_2,0)+COALESCE(count_3,0)+COALESCE(count_4,0)+COALESCE(count_5,0))::float
                                 ELSE 1.0 END
                            ELSE 0 END)::float AS dept_w
                FROM trace_scores
                WHERE (course_id, instructor_id, term_id) IN %s
                GROUP BY question
            """, (term_keys,))

            if not radar_rows:
                continue

            agg_scores = [
                {"question": str(r["question"] or "").strip(), "mean": float(r["prof_sum"]) / float(r["prof_w"])}
                for r in radar_rows if float(r["prof_w"] or 0) > 0
            ]
            dept_scores = [
                {"question": str(r["question"] or "").strip(), "mean": float(r["dept_sum"]) / float(r["dept_w"])}
                for r in radar_rows if float(r["dept_w"] or 0) > 0
            ]

            # Older terms may not have dept_mean in scores rows — fall back to dept avg query
            if not dept_scores:
                dept_name = next(
                    (str(tc["department_name"] or "") for tc in trace_course_rows
                     if (int(tc["term_id"]) if tc["term_id"] else 0) == tid),
                    ""
                )
                if dept_name and tid:
                    dept_rows = query("""
                        SELECT ts.question,
                               SUM(1*COALESCE(ts.count_1,0)+2*COALESCE(ts.count_2,0)
                                   +3*COALESCE(ts.count_3,0)+4*COALESCE(ts.count_4,0)
                                   +5*COALESCE(ts.count_5,0)) AS weighted_sum,
                               SUM(COALESCE(ts.count_1,0)+COALESCE(ts.count_2,0)
                                   +COALESCE(ts.count_3,0)+COALESCE(ts.count_4,0)
                                   +COALESCE(ts.count_5,0)) AS total_responses
                        FROM trace_scores ts
                        JOIN trace_courses tc
                            ON ts.course_id=tc.course_id
                           AND ts.instructor_id=tc.instructor_id
                           AND ts.term_id=tc.term_id
                        WHERE tc.department_name=%s AND tc.term_id=%s
                        GROUP BY ts.question
                    """, (dept_name, tid))
                    for r in dept_rows:
                        total = int(r["total_responses"] or 0)
                        wsum = float(r["weighted_sum"] or 0)
                        if total > 0:
                            dept_scores.append({"question": str(r["question"] or ""), "mean": wsum / total})

            points = []
            has_data = False
            for m in _RADAR_METRICS:
                prof_val = _get_radar_metric_value(agg_scores, m["patterns"])
                dept_val = _get_radar_metric_value(dept_scores, m["patterns"]) if dept_scores else None
                if prof_val is not None:
                    has_data = True
                points.append({
                    "metric": m["metric"],
                    "professor": prof_val if prof_val is not None else 0,
                    "department": dept_val if dept_val is not None else 0,
                    "profMissing": prof_val is None,
                    "deptMissing": dept_val is None,
                })

            if has_data:
                radar_data = points
                radar_term_title = next(
                    (str(tc["term_title"] or "") for tc in trace_course_rows
                     if (int(tc["term_id"]) if tc["term_id"] else 0) == tid),
                    ""
                )
                break

        profile["radarData"] = radar_data
        profile["radarTermTitle"] = radar_term_title if radar_data else None

    else:
        # Lightweight query: only challenging scores to compute the professor-wide avg and per-course avg
        challeng_rows = query("""
            SELECT ts.course_id, ts.term_id, ts.mean, ts.count_1, ts.count_2, ts.count_3, ts.count_4, ts.count_5
            FROM trace_scores ts
            JOIN trace_courses tc
              ON ts.course_id = tc.course_id
             AND ts.instructor_id = tc.instructor_id
             AND ts.term_id = tc.term_id
            WHERE tc.name_key = %s AND lower(ts.question) LIKE '%%challeng%%'
        """, (name_key,))

        challeng_sum, challeng_weight = 0.0, 0
        challeng_by_ct = {}
        for s in challeng_rows:
            c1 = int(s["count_1"] or 0)
            c2 = int(s["count_2"] or 0)
            c3 = int(s["count_3"] or 0)
            c4 = int(s["count_4"] or 0)
            c5 = int(s["count_5"] or 0)
            total_resp = c1 + c2 + c3 + c4 + c5
            key = (int(s["course_id"]), int(s["term_id"] or 0))
            if key not in challeng_by_ct:
                challeng_by_ct[key] = {"sum": 0.0, "weight": 0}
            if total_resp > 0:
                computed_mean = (1*c1 + 2*c2 + 3*c3 + 4*c4 + 5*c5) / total_resp
                challeng_sum += computed_mean * total_resp
                challeng_weight += total_resp
                challeng_by_ct[key]["sum"] += computed_mean * total_resp
                challeng_by_ct[key]["weight"] += total_resp
            elif s["mean"]:
                challeng_sum += float(s["mean"])
                challeng_weight += 1
                challeng_by_ct[key]["sum"] += float(s["mean"])
                challeng_by_ct[key]["weight"] += 1

        trace_avg_difficulty = round(challeng_sum / challeng_weight, 2) if challeng_weight > 0 else None

        # Compute rating distribution from overall rating question for unauthenticated users
        overall_rows = query("""
            SELECT tc.display_name, ts.completed, ts.count_1, ts.count_2, ts.count_3, ts.count_4, ts.count_5
            FROM trace_scores ts
            JOIN trace_courses tc
              ON ts.course_id = tc.course_id
             AND ts.instructor_id = tc.instructor_id
             AND ts.term_id = tc.term_id
            WHERE tc.name_key = %s AND lower(ts.question) LIKE '%%overall%%'
        """, (name_key,))
        rating_dist_by_course = {}
        for s in overall_rows:
            dn = str(s["display_name"] or "")
            m = re.match(r"^([A-Z]+\d+)", dn)
            course_code = (m.group(1) if m else dn.split(":")[0].split(" ")[0]).upper()
            if course_code not in rating_dist_by_course:
                rating_dist_by_course[course_code] = {"count1": 0, "count2": 0, "count3": 0, "count4": 0, "count5": 0, "completed": 0}
            rating_dist_by_course[course_code]["count1"] += int(s["count_1"] or 0)
            rating_dist_by_course[course_code]["count2"] += int(s["count_2"] or 0)
            rating_dist_by_course[course_code]["count3"] += int(s["count_3"] or 0)
            rating_dist_by_course[course_code]["count4"] += int(s["count_4"] or 0)
            rating_dist_by_course[course_code]["count5"] += int(s["count_5"] or 0)
            rating_dist_by_course[course_code]["completed"] += int(s["completed"] or 0)
        profile["traceRatingCounts"] = rating_dist_by_course
        profile["radarData"] = None

        hours_rows = query("""
            SELECT ts.course_id, ts.term_id, ts.mean,
                   ts.count_1, ts.count_2, ts.count_3, ts.count_4, ts.count_5
            FROM trace_scores ts
            JOIN trace_courses tc
              ON ts.course_id = tc.course_id
             AND ts.instructor_id = tc.instructor_id
             AND ts.term_id = tc.term_id
            WHERE tc.name_key = %s AND lower(ts.question) LIKE '%%hours%%'
        """, (name_key,))
        hours_by_ct = {}
        for s in hours_rows:
            key = (int(s["course_id"]), int(s["term_id"] or 0))
            c1 = int(s["count_1"] or 0); c2 = int(s["count_2"] or 0)
            c3 = int(s["count_3"] or 0); c4 = int(s["count_4"] or 0)
            c5 = int(s["count_5"] or 0)
            total_resp = c1 + c2 + c3 + c4 + c5
            if key not in hours_by_ct:
                hours_by_ct[key] = {"sum": 0.0, "weight": 0}
            if total_resp > 0:
                hours_by_ct[key]["sum"] += (1*c1 + 3.5*c2 + 6*c3 + 9*c4 + 12*c5)
                hours_by_ct[key]["weight"] += total_resp
            elif s["mean"]:
                hours_by_ct[key]["sum"] += float(s["mean"])
                hours_by_ct[key]["weight"] += 1

        for c in trace_course_rows:
            cid = int(c["course_id"]); tid = int(c["term_id"]) if c["term_id"] else 0
            h = hours_by_ct.get((cid, tid))
            course_hours = round(h["sum"] / h["weight"], 1) if h and h["weight"] > 0 else None
            ch = challeng_by_ct.get((cid, tid))
            trace_course_list.append({
                "courseId": cid,
                "termId": tid,
                "termTitle": str(c["term_title"] or ""),
                "departmentName": str(c["department_name"] or ""),
                "displayName": str(c["display_name"] or ""),
                "hoursPerWeek": course_hours,
                "challengeWeightedSum": ch["sum"] if ch and ch["weight"] > 0 else None,
                "challengeResponses": ch["weight"] if ch and ch["weight"] > 0 else None,
            })

    # Blend RMP difficulty with TRACE challenging avg into a single difficulty value
    rmp_diff = round(prof["difficulty"], 2) if prof["difficulty"] else None
    if rmp_diff is not None and trace_avg_difficulty is not None:
        profile["difficulty"] = round((rmp_diff + trace_avg_difficulty) / 2, 2)
    elif trace_avg_difficulty is not None:
        profile["difficulty"] = trace_avg_difficulty
    # else: profile["difficulty"] already set to rmp_diff (or None) above

    profile["traceCourses"] = trace_course_list

    cache_set(cache_key, profile)
    resp = jsonify(profile)
    resp.headers["Cache-Control"] = "private, max-age=3600" if is_authed else "public, max-age=3600"
    resp.headers["Vary"] = "Authorization"
    return resp


@app.route("/api/professors/<slug>/reviews")
def professor_reviews(slug):
    is_authed = False
    token = _get_auth_token()
    if token:
        try:
            pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            is_authed = True
        except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
            pass

    cache_key = f"prof_reviews:{slug}:{'a' if is_authed else 'u'}"
    cached = cache_get(cache_key)
    if cached:
        resp = jsonify(cached)
        resp.headers["Cache-Control"] = "private, max-age=3600" if is_authed else "public, max-age=3600"
        resp.headers["Vary"] = "Authorization"
        return resp

    prof = query_one("SELECT name_key FROM professors_catalog WHERE slug = %s", (slug,))
    if not prof:
        name_key = slug.strip().lower().replace("-", " ")
        prof = query_one("SELECT name_key FROM professors_catalog WHERE name_key = %s", (name_key,))
    if not prof:
        return jsonify({"error": "Professor not found"}), 404

    name_key = prof["name_key"]

    # ── RMP reviews ──
    review_rows = query("""
        SELECT course, quality, difficulty, date, tags, attendance, grade,
               textbook, online_class, comment
        FROM rmp_reviews WHERE name_key = %s
    """, (name_key,))

    reviews = []
    for r in review_rows:
        reviews.append({
            "course": str(r["course"] or ""),
            "quality": int(r["quality"]) if r["quality"] else 0,
            "difficulty": int(r["difficulty"]) if r["difficulty"] else 0,
            "date": str(r["date"] or ""),
            "tags": str(r["tags"] or ""),
            "attendance": str(r["attendance"] or ""),
            "grade": str(r["grade"] or ""),
            "textbook": str(r["textbook"] or ""),
            "online_class": str(r["online_class"] or ""),
            "comment": sanitize(r["comment"]) if r["comment"] else "",
        })

    # ── TRACE comments ──
    trace_course_rows = query(
        "SELECT course_id, term_id, instructor_id FROM trace_courses WHERE name_key = %s",
        (name_key,)
    )

    comments = []
    if trace_course_rows:
        keys = set()
        for c in trace_course_rows:
            keys.add((int(c["course_id"]), int(c["instructor_id"]), int(c["term_id"]) if c["term_id"] else 0))

        if keys:
            comment_rows = query(
                "SELECT tc_term_id, tc_course_id, question, comment FROM trace_comments "
                "WHERE (tc_course_id, tc_instructor_id, tc_term_id) IN %s",
                (tuple(keys),)
            )
            # Group by question so we can deduplicate near-identical comments per group
            by_question: dict = {}
            for c in comment_rows:
                comment_text = sanitize(c["comment"]) if c["comment"] else ""
                if not comment_text.strip():
                    continue
                q = str(c["question"] or "")
                by_question.setdefault(q, []).append({
                    "question": q,
                    "comment": comment_text,
                    "termId": int(c["tc_term_id"]) if c["tc_term_id"] else 0,
                    "courseId": int(c["tc_course_id"]) if c["tc_course_id"] else 0,
                })

            def _normalize(s: str) -> str:
                return re.sub(r'\s+', ' ', s.lower()).strip()

            def _dedup_group(items: list) -> list:
                seen: set[str] = set()
                result = []
                for item in items:
                    norm = _normalize(item["comment"])
                    # Use truncated prefix as hash key for O(1) lookup instead of O(n) scan
                    prefix_key = norm[:80]
                    if prefix_key in seen:
                        continue
                    seen.add(prefix_key)
                    result.append(item)
                return result

            for q, items in by_question.items():
                for item in _dedup_group(items):
                    comments.append({
                        "question": item["question"],
                        "comment": item["comment"] if is_authed else "",
                        "termId": item["termId"],
                        "courseId": item["courseId"],
                    })

    result = {"reviews": reviews, "traceComments": comments}
    cache_set(cache_key, result)
    resp = jsonify(result)
    resp.headers["Cache-Control"] = "private, max-age=3600" if is_authed else "public, max-age=3600"
    resp.headers["Vary"] = "Authorization"
    return resp


@app.route("/api/professors/<slug>/full")
def professor_full(slug):
    """Combined profile + reviews in one request to halve round-trips."""
    profile_resp = professor_profile(slug)
    if isinstance(profile_resp, tuple):
        return profile_resp  # propagate 404/errors

    reviews_resp = professor_reviews(slug)
    if isinstance(reviews_resp, tuple):
        reviews_data = {"reviews": [], "traceComments": []}
    else:
        reviews_data = reviews_resp.get_json()

    profile_data = profile_resp.get_json()
    profile_data["reviews"] = reviews_data.get("reviews", [])
    profile_data["traceComments"] = reviews_data.get("traceComments", [])

    is_authed = False
    token = _get_auth_token()
    if token:
        try:
            pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            is_authed = True
        except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
            pass

    resp = jsonify(profile_data)
    resp.headers["Cache-Control"] = "private, max-age=3600" if is_authed else "public, max-age=3600"
    resp.headers["Vary"] = "Authorization"
    return resp


@app.route("/api/departments")
def departments():
    college = request.args.get("college", "")
    cache_key = f"depts:{college or 'all'}"
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)
    if college and college != "All":
        college_list = [c.strip() for c in college.split(",") if c.strip()]
        if len(college_list) == 1:
            rows = query("""
                SELECT DISTINCT department FROM professors_catalog
                WHERE avg_rating IS NOT NULL AND college = %s
                ORDER BY department
            """, (college_list[0],))
        else:
            rows = query("""
                SELECT DISTINCT department FROM professors_catalog
                WHERE avg_rating IS NOT NULL AND college IN (""" + ",".join(["%s"] * len(college_list)) + """)
                ORDER BY department
            """, tuple(college_list))
    else:
        rows = query("""
            SELECT DISTINCT department FROM professors_catalog
            WHERE avg_rating IS NOT NULL
            ORDER BY department
        """)
    BAD_DEPTS = {"Computer amp Informational Tech.", "Computer  Informational Tech.", "Counseling amp Educational Psych", "Counseling  Educational Psych"}
    result = [r['department'] for r in rows if r['department'] and r['department'] not in BAD_DEPTS]
    cache_set(cache_key, result)
    return jsonify(result)


@app.route("/api/professors-catalog")
def professors_catalog():
    q = normalize_name(request.args.get("q", ""))
    college = request.args.get("college", "")
    dept = request.args.get("dept", "")
    sort = request.args.get("sort", "alpha")
    page = int(request.args.get("page", "1"))
    limit = min(int(request.args.get("limit", "20")), 10000)

    try:
        min_rating = float(request.args.get("minRating", "0"))
    except (ValueError, TypeError):
        min_rating = 0.0
    try:
        max_rating = float(request.args.get("maxRating", "5"))
    except (ValueError, TypeError):
        max_rating = 5.0
    try:
        min_reviews = int(request.args.get("minReviews", "1"))
    except (ValueError, TypeError):
        min_reviews = 1
    max_reviews_raw = request.args.get("maxReviews")
    try:
        max_reviews = int(max_reviews_raw) if max_reviews_raw is not None else None
    except (ValueError, TypeError):
        max_reviews = None

    cache_key = f"profcat:{q}:{college}:{dept}:{sort}:{page}:{limit}:{min_rating}:{max_rating}:{min_reviews}:{max_reviews}"
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    # If there's a search query, get matching name_keys first
    matched_name_keys = None
    if q and len(q) >= 2:
        matches = _professor_search(q, limit=200)
        matched_name_keys = [m["name_key"] for m in matches]
        if not matched_name_keys:
            return jsonify({"professors": [], "total": 0, "page": 1, "totalPages": 1})

    # Build dynamic query
    conditions = ["avg_rating IS NOT NULL"]
    params = []

    if college and college != "All":
        college_list = [c.strip() for c in college.split(",") if c.strip()]
        if len(college_list) == 1:
            conditions.append("college = %s")
            params.append(college_list[0])
        elif college_list:
            conditions.append("college IN (" + ",".join(["%s"] * len(college_list)) + ")")
            params.extend(college_list)
    DEPT_ALIASES = {
        "Computer & Informational Tech.": ["Computer amp Informational Tech.", "Computer  Informational Tech."],
        "Counseling & Educational Psych": ["Counseling amp Educational Psych", "Counseling  Educational Psych"],
    }
    if dept and dept != "All":
        dept_list = [d.strip() for d in dept.split(",") if d.strip()]
        expanded = []
        for d in dept_list:
            expanded.append(d)
            expanded.extend(DEPT_ALIASES.get(d, []))
        if len(expanded) == 1:
            conditions.append("department = %s")
            params.append(expanded[0])
        elif expanded:
            conditions.append("department IN (" + ",".join(["%s"] * len(expanded)) + ")")
            params.extend(expanded)
    if min_rating > 0:
        conditions.append("avg_rating >= %s")
        params.append(min_rating)
    if max_rating < 5:
        conditions.append("avg_rating <= %s")
        params.append(max_rating)
    if min_reviews > 1:
        conditions.append("total_reviews >= %s")
        params.append(min_reviews)
    if max_reviews is not None:
        conditions.append("total_reviews <= %s")
        params.append(max_reviews)

    where = " AND ".join(conditions)

    if matched_name_keys is not None:
        # Filter to search matches, preserve search order
        placeholders = ",".join(["%s"] * len(matched_name_keys))
        # Get total count
        count_row = query_one(
            f"SELECT COUNT(*) as cnt FROM professors_catalog WHERE {where} AND name_key IN ({placeholders})",
            params + matched_name_keys
        )
        total = count_row["cnt"]

        # Get page data - preserve search ranking order
        total_pages = max(1, (total + limit - 1) // limit)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * limit

        all_rows = query(
            f"SELECT * FROM professors_catalog WHERE {where} AND name_key IN ({placeholders})",
            params + matched_name_keys
        )

        # Reorder by search ranking
        name_key_order = {nk: i for i, nk in enumerate(matched_name_keys)}
        all_rows.sort(key=lambda r: name_key_order.get(r["name_key"], 999999))
        page_rows = all_rows[offset:offset + limit]
    else:
        # No search - use SQL sorting
        if sort == "rating":
            order = "avg_rating DESC NULLS LAST"
        elif sort == "comments":
            order = "total_comments DESC"
        else:
            order = "lower(name) ASC"

        count_row = query_one(f"SELECT COUNT(*) as cnt FROM professors_catalog WHERE {where}", params)
        total = count_row["cnt"]

        total_pages = max(1, (total + limit - 1) // limit)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * limit

        page_rows = query(
            f"SELECT * FROM professors_catalog WHERE {where} ORDER BY {order} LIMIT %s OFFSET %s",
            params + [limit, offset]
        )

    professors = []
    for row in page_rows:
        professors.append({
            "name": row["name"],
            "slug": row["slug"],
            "department": row["department"],
            "college": row["college"],
            "avgRating": round(row["avg_rating"], 2) if row["avg_rating"] else None,
            "rmpRating": round(row["rmp_rating"], 2) if row["rmp_rating"] else None,
            "traceRating": round(row["trace_rating"], 2) if row["trace_rating"] else None,
            "totalReviews": row["total_reviews"],
            "totalComments": row.get("total_comments", 0) or 0,
            "wouldTakeAgainPct": round(row["would_take_again_pct"], 1) if row["would_take_again_pct"] else None,
            "imageUrl": row["image_url"],
        })

    result = {
        "professors": professors,
        "total": total,
        "page": page,
        "totalPages": total_pages,
    }
    cache_set(cache_key, result)
    return jsonify(result)


@app.route("/api/course-departments")
def course_departments():
    cached = cache_get("course_depts")
    if cached:
        return jsonify(cached)
    rows = query("""
        SELECT DISTINCT department FROM course_catalog
        WHERE department IS NOT NULL AND department != ''
        ORDER BY department
    """)
    result = [r["department"] for r in rows]
    cache_set("course_depts", result)
    return jsonify(result)


@app.route("/api/courses-catalog")
def courses_catalog():
    q = normalize_name(request.args.get("q", ""))
    dept = request.args.get("dept", "")
    sort = request.args.get("sort", "alpha")
    page = int(request.args.get("page", "1"))
    limit = min(int(request.args.get("limit", "20")), 10000)

    try:
        min_rating = float(request.args.get("minRating", "0"))
    except (ValueError, TypeError):
        min_rating = 0.0

    try:
        max_rating = float(request.args.get("maxRating", "5"))
    except (ValueError, TypeError):
        max_rating = 5.0

    cache_key = f"coursecat:{q}:{dept}:{sort}:{page}:{limit}:{min_rating}:{max_rating}"
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    # Query course_catalog for listing, then bulk-fetch ratings
    count_where = []
    count_params = []

    if dept and dept != "All":
        dept_list = [d.strip() for d in dept.split(",") if d.strip()]
        if len(dept_list) == 1:
            count_where.append("department = %s")
            count_params.append(dept_list[0])
        elif dept_list:
            count_where.append("department IN (" + ",".join(["%s"] * len(dept_list)) + ")")
            count_params.extend(dept_list)
    if q:
        count_where.append("search_text LIKE %s")
        count_params.append(f"%{q}%")

    where_str = ("WHERE " + " AND ".join(count_where)) if count_where else ""

    rating_filter_active = min_rating > 0 or max_rating < 5

    # When rating filters or rating sort are active, we need all rows so we can
    # compute ratings first, filter/sort, then paginate in Python.
    if rating_filter_active or sort == "rating":
        rows = query(f"""
            SELECT code, name, department FROM course_catalog
            {where_str}
            ORDER BY lower(code) ASC
        """, count_params)

        # Bulk-fetch ratings for ALL matching courses
        rating_map = {}
        if rows:
            codes = [r["code"] for r in rows]
            placeholders = ",".join(["%s"] * len(codes))
            rating_rows = query(f"""
                SELECT
                    tc.course_code,
                    SUM(CAST(ts.mean AS FLOAT) * CAST(ts.total_responses AS FLOAT)) AS weighted_sum,
                    SUM(CAST(ts.total_responses AS FLOAT)) AS total_responses
                FROM trace_courses tc
                JOIN trace_scores ts
                    ON tc.course_id = ts.course_id
                    AND tc.instructor_id = ts.instructor_id
                    AND tc.term_id = ts.term_id
                WHERE LOWER(ts.question) LIKE '%%overall%%'
                    AND tc.course_code IN ({placeholders})
                GROUP BY tc.course_code
            """, codes)
            for rr in rating_rows:
                tr = _safe_float(rr["total_responses"])
                rating_map[rr["course_code"]] = (
                    _safe_float(rr["weighted_sum"]) / tr if tr > 0 else None
                )

        # Build course list with ratings
        courses = []
        for r in rows:
            avg = rating_map.get(r["code"])
            # Apply rating filter
            if rating_filter_active:
                if avg is None:
                    continue
                if min_rating > 0 and avg < min_rating:
                    continue
                if max_rating < 5 and avg > max_rating:
                    continue
            courses.append({
                "code": r["code"],
                "name": r["name"],
                "department": r["department"],
                "avgRating": avg,
            })

        # Sort by rating if requested
        if sort == "rating":
            courses.sort(key=lambda c: (c["avgRating"] is None, -(c["avgRating"] or 0)))

        # Paginate in Python
        total = len(courses)
        total_pages = max(1, (total + limit - 1) // limit)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * limit
        courses = courses[offset:offset + limit]
    else:
        # No rating filter — use SQL pagination directly
        count_row = query_one(f"SELECT COUNT(*) as cnt FROM course_catalog {where_str}", count_params)
        total = count_row["cnt"] if count_row else 0

        total_pages = max(1, (total + limit - 1) // limit)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * limit

        sort_map = {
            "sections": "code ASC",
            "recent": "code ASC",
            "alpha": "lower(code) ASC",
        }
        order = sort_map.get(sort, "lower(code) ASC")

        rows = query(f"""
            SELECT code, name, department FROM course_catalog
            {where_str}
            ORDER BY {order}
            LIMIT %s OFFSET %s
        """, count_params + [limit, offset])

        # Bulk-fetch ratings for this page of courses
        rating_map = {}
        if rows:
            codes = [r["code"] for r in rows]
            placeholders = ",".join(["%s"] * len(codes))
            rating_rows = query(f"""
                SELECT
                    tc.course_code,
                    SUM(CAST(ts.mean AS FLOAT) * CAST(ts.total_responses AS FLOAT)) AS weighted_sum,
                    SUM(CAST(ts.total_responses AS FLOAT)) AS total_responses
                FROM trace_courses tc
                JOIN trace_scores ts
                    ON tc.course_id = ts.course_id
                    AND tc.instructor_id = ts.instructor_id
                    AND tc.term_id = ts.term_id
                WHERE LOWER(ts.question) LIKE '%%overall%%'
                    AND tc.course_code IN ({placeholders})
                GROUP BY tc.course_code
            """, codes)
            for rr in rating_rows:
                tr = _safe_float(rr["total_responses"])
                rating_map[rr["course_code"]] = (
                    _safe_float(rr["weighted_sum"]) / tr if tr > 0 else None
                )

        courses = []
        for r in rows:
            courses.append({
                "code": r["code"],
                "name": r["name"],
                "department": r["department"],
                "avgRating": rating_map.get(r["code"]),
            })

    result = {
        "courses": courses,
        "total": total,
        "page": page,
        "totalPages": total_pages,
    }
    cache_set(cache_key, result)
    return jsonify(result)


@app.route("/api/courses/<code>")
def course_profile(code):
    code_norm = _format_course_code(code)
    if not code_norm:
        return jsonify({"error": "Course not found"}), 404

    is_authed = False
    token = _get_auth_token()
    if token:
        try:
            pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            is_authed = True
        except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
            pass

    cache_key = f"course:{code_norm}:{'a' if is_authed else 'u'}"
    cached = cache_get(cache_key)
    if cached:
        resp = jsonify(cached)
        resp.headers["Cache-Control"] = "private, max-age=3600" if is_authed else "public, max-age=3600"
        resp.headers["Vary"] = "Authorization"
        return resp

    # Look up course in catalog
    course = query_one("SELECT code, name, department FROM course_catalog WHERE code = %s", (code_norm,))
    if not course:
        return jsonify({"error": "Course not found"}), 404

    # Get all sections for this course from trace_courses using indexed course_code column
    sections = query("""
        SELECT DISTINCT ON (tc.course_id, tc.instructor_id, tc.term_id)
            tc.course_id, tc.instructor_id, tc.term_id, tc.term_title,
            tc.department_name, tc.display_name, tc.section, tc.enrollment,
            tc.instructor_first_name, tc.instructor_last_name
        FROM trace_courses tc
        WHERE tc.course_code = %s
        ORDER BY tc.course_id, tc.instructor_id, tc.term_id, tc.term_id DESC
    """, (code_norm,))

    if not sections:
        return jsonify({"error": "Course not found"}), 404

    # Single query for all score types using conditional aggregation (replaces 3 separate queries)
    section_keys = tuple((s["course_id"], s["instructor_id"], s["term_id"]) for s in sections)
    combined_scores = query(
        "SELECT course_id, instructor_id, term_id, "
        "SUM(CASE WHEN lower(question) LIKE '%%overall%%' THEN CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT) ELSE 0 END) as overall_weighted, "
        "SUM(CASE WHEN lower(question) LIKE '%%overall%%' THEN CAST(total_responses AS INT) ELSE 0 END) as overall_responses, "
        "SUM(CASE WHEN lower(question) LIKE '%%overall%%' THEN completed ELSE 0 END) as overall_completed, "
        "SUM(CASE WHEN lower(question) LIKE '%%challeng%%' THEN CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT) ELSE 0 END) as challeng_weighted, "
        "SUM(CASE WHEN lower(question) LIKE '%%challeng%%' THEN CAST(total_responses AS INT) ELSE 0 END) as challeng_responses, "
        "SUM(CASE WHEN lower(question) LIKE '%%hours%%' THEN CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT) ELSE 0 END) as hours_weighted, "
        "SUM(CASE WHEN lower(question) LIKE '%%hours%%' THEN CAST(total_responses AS INT) ELSE 0 END) as hours_responses "
        "FROM trace_scores "
        "WHERE (course_id, instructor_id, term_id) IN %s "
        "AND (lower(question) LIKE '%%overall%%' OR lower(question) LIKE '%%challeng%%' OR lower(question) LIKE '%%hours%%') "
        "GROUP BY course_id, instructor_id, term_id",
        (section_keys,)
    )

    # Build score maps from combined result
    score_map = {}
    challenging_map = {}
    hours_map = {}
    for row in combined_scores:
        key = (row["course_id"], row["instructor_id"], row["term_id"])
        if row["overall_responses"]:
            score_map[key] = {
                "weighted_sum": row["overall_weighted"],
                "total_responses": row["overall_responses"],
                "completed": row["overall_completed"],
            }
        if row["challeng_responses"]:
            challenging_map[key] = {
                "weighted_sum": row["challeng_weighted"],
                "total_responses": row["challeng_responses"],
            }
        if row["hours_responses"]:
            hours_map[key] = {
                "weighted_sum": row["hours_weighted"],
                "total_responses": row["hours_responses"],
            }

    # Compute summary
    total_weighted = 0.0
    total_responses = 0
    total_enrollment = 0
    total_sections_with_enrollment = 0
    latest_term_id = 0
    latest_term_title = ""
    latest_term_sort = -1

    for s in sections:
        enrollment = _safe_int(s["enrollment"])
        if enrollment > 0:
            total_enrollment += enrollment
            total_sections_with_enrollment += 1
        tid = _safe_int(s["term_id"])
        tsort = term_sort_key(s["term_title"] or "")
        if tsort > latest_term_sort:
            latest_term_sort = tsort
            latest_term_id = tid
            latest_term_title = s["term_title"] or ""
        key = (s["course_id"], s["instructor_id"], s["term_id"])
        if key in score_map:
            total_weighted += _safe_float(score_map[key]["weighted_sum"])
            total_responses += _safe_int(score_map[key]["total_responses"])

    avg_rating = (total_weighted / total_responses) if total_responses > 0 else None

    summary = {
        "code": course["code"],
        "name": course["name"],
        "department": course["department"] or "",
        "avgRating": round(avg_rating, 2) if avg_rating is not None else None,
        "avgEnrollment": round(total_enrollment / total_sections_with_enrollment) if total_sections_with_enrollment > 0 else None,
        "latestTermTitle": latest_term_title,
    }

    # Build instructor aggregates
    instructor_data = {}
    for s in sections:
        fname = (s["instructor_first_name"] or "").strip()
        lname = (s["instructor_last_name"] or "").strip()
        name = f"{fname} {lname}".strip()
        if not name:
            continue
        if name not in instructor_data:
            instructor_data[name] = {
                "sections": 0, "enrollment": 0,
                "weighted": 0.0, "responses": 0,
                "challeng_weighted": 0.0, "challeng_responses": 0,
                "hours_weighted": 0.0, "hours_responses": 0,
                "latest_term_title": "", "latest_term_sort": -1,
            }
        tsort = term_sort_key(s["term_title"] or "")
        if tsort > instructor_data[name]["latest_term_sort"]:
            instructor_data[name]["latest_term_sort"] = tsort
            instructor_data[name]["latest_term_title"] = s["term_title"] or ""
        instructor_data[name]["sections"] += 1
        instructor_data[name]["enrollment"] += _safe_int(s["enrollment"])
        key = (s["course_id"], s["instructor_id"], s["term_id"])
        if key in score_map:
            instructor_data[name]["weighted"] += _safe_float(score_map[key]["weighted_sum"])
            instructor_data[name]["responses"] += _safe_int(score_map[key]["total_responses"])
        if key in challenging_map:
            instructor_data[name]["challeng_weighted"] += _safe_float(challenging_map[key]["weighted_sum"])
            instructor_data[name]["challeng_responses"] += _safe_int(challenging_map[key]["total_responses"])
        if key in hours_map:
            instructor_data[name]["hours_weighted"] += _safe_float(hours_map[key]["weighted_sum"])
            instructor_data[name]["hours_responses"] += _safe_int(hours_map[key]["total_responses"])

    # Look up instructor metadata from professors_catalog (batched)
    name_key_map = {normalize_name(name): name for name in instructor_data}
    name_keys = list(name_key_map.keys())
    prof_map = {}
    comment_counts = {}
    rmp_course_diff_map = {}
    if name_keys:
        placeholders = ",".join(["%s"] * len(name_keys))
        prof_rows = query(
            f"SELECT name_key, slug, image_url, total_reviews, would_take_again_pct, difficulty, rmp_rating "
            f"FROM professors_catalog WHERE name_key IN ({placeholders})", name_keys
        )
        prof_map = {r["name_key"]: r for r in prof_rows}
        # Fuzzy match RMP course: exact normalized match, or match on numeric portion
        # (RMP course names are often misspelled, e.g. "C1100" instead of "CS1100")
        code_num = re.sub(r"[^0-9]", "", code_norm)
        rmp_course_diff_rows = query(
            f"SELECT name_key, AVG(CAST(difficulty AS FLOAT)) as avg_diff "
            f"FROM rmp_reviews "
            f"WHERE name_key IN ({placeholders}) AND difficulty IS NOT NULL "
            f"AND (UPPER(REPLACE(course, ' ', '')) = %s OR REGEXP_REPLACE(course, '[^0-9]', '', 'g') = %s) "
            f"GROUP BY name_key",
            name_keys + [code_norm, code_num]
        )
        rmp_course_diff_map = {r["name_key"]: round(float(r["avg_diff"]), 2) for r in rmp_course_diff_rows if r["avg_diff"] is not None}
        combined_counts = query(
            f"SELECT name_key, SUM(cnt) as cnt FROM ("
            f"  SELECT name_key, COUNT(*) as cnt FROM rmp_reviews "
            f"  WHERE name_key IN ({placeholders}) AND comment IS NOT NULL AND comment != '' "
            f"  GROUP BY name_key"
            f"  UNION ALL "
            f"  SELECT tc2.name_key, COUNT(*) as cnt "
            f"  FROM trace_comments tc "
            f"  JOIN trace_courses tc2 ON tc.tc_course_id = tc2.course_id "
            f"    AND tc.tc_instructor_id = tc2.instructor_id "
            f"    AND tc.tc_term_id = tc2.term_id "
            f"  WHERE tc2.name_key IN ({placeholders}) "
            f"  AND tc.comment IS NOT NULL AND tc.comment != '' "
            f"  GROUP BY tc2.name_key"
            f") sub GROUP BY name_key",
            name_keys + name_keys
        )
        for r in combined_counts:
            comment_counts[r["name_key"]] = int(r["cnt"])

    instructor_rows = []
    for name, data in instructor_data.items():
        prof = prof_map.get(normalize_name(name))
        nk = normalize_name(name)
        meta_slug = prof["slug"] if prof else ""
        meta_image = prof["image_url"] if prof else None
        meta_reviews = prof["total_reviews"] if prof else 0
        meta_wta = round(prof["would_take_again_pct"], 1) if prof and prof["would_take_again_pct"] else None
        meta_diff = round(prof["difficulty"], 2) if prof and prof["difficulty"] else None
        meta_comments = comment_counts.get(nk, 0)

        resp = data["responses"]
        challeng_resp = data["challeng_responses"]
        hours_resp = data["hours_responses"]
        trace_diff = round(data["challeng_weighted"] / challeng_resp, 2) if challeng_resp > 0 else None
        rmp_course_diff = rmp_course_diff_map.get(nk)
        if trace_diff is not None and rmp_course_diff is not None:
            course_diff = round((trace_diff + rmp_course_diff) / 2, 2)
        elif trace_diff is not None:
            course_diff = trace_diff
        else:
            course_diff = rmp_course_diff
        instructor_rows.append({
            "name": name,
            "slug": meta_slug,
            "imageUrl": meta_image,
            "difficulty": meta_diff,
            "wouldTakeAgainPct": meta_wta,
            "totalReviews": meta_reviews or 0,
            "totalComments": meta_comments,
            "_sections": data["sections"],
            "latestTermTitle": data["latest_term_title"],
            "avgRating": round(data["weighted"] / resp, 2) if resp > 0 else None,
            "courseAvgDifficulty": course_diff,
            "courseAvgHoursPerWeek": round(data["hours_weighted"] / hours_resp, 2) if hours_resp > 0 else None,
        })
    instructor_rows.sort(key=lambda r: (r["avgRating"] is None, -(r["avgRating"] or 0), -r["_sections"]))
    for row in instructor_rows:
        del row["_sections"]

    # Build section rows
    section_rows = []
    for s in sorted(sections, key=lambda x: -(x["term_id"] or 0)):
        key = (s["course_id"], s["instructor_id"], s["term_id"])
        sc = score_map.get(key)
        fname = (s["instructor_first_name"] or "").strip()
        lname = (s["instructor_last_name"] or "").strip()
        name = f"{fname} {lname}".strip()
        overall_mean = None
        if sc and _safe_int(sc["total_responses"]) > 0:
            overall_mean = round(_safe_float(sc["weighted_sum"]) / _safe_int(sc["total_responses"]), 2)
        prof = prof_map.get(normalize_name(name))
        rmp_rating = round(prof["rmp_rating"], 2) if prof and prof.get("rmp_rating") else None
        section_rows.append({
            "termId": _safe_int(s["term_id"]),
            "termTitle": s["term_title"] or "",
            "instructor": name,
            "overallRating": overall_mean if is_authed else None,
            "rmpRating": rmp_rating if is_authed else None,
        })

    # Get question-level scores
    question_rows = []
    q_scores = query(
        "SELECT question, "
        "SUM(CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT)) as weighted_sum, "
        "SUM(total_responses) as total_responses "
        "FROM trace_scores "
        "WHERE (course_id, instructor_id, term_id) IN %s "
        "GROUP BY question",
        (section_keys,)
    )
    for qs in q_scores:
        resp = _safe_int(qs["total_responses"])
        question_rows.append({
            "question": qs["question"],
            "avgRating": round(_safe_float(qs["weighted_sum"]) / resp, 2) if resp > 0 else None,
            "_totalResponses": resp,
        })
    question_rows.sort(key=lambda r: (-r["_totalResponses"], r["question"].lower()))
    for row in question_rows:
        del row["_totalResponses"]

    result = {
        "summary": summary,
        "instructors": instructor_rows,
        "sections": section_rows if is_authed else [],
        "questionScores": question_rows if is_authed else [],
    }
    cache_set(cache_key, result)
    resp = jsonify(result)
    resp.headers["Cache-Control"] = "private, max-age=3600" if is_authed else "public, max-age=3600"
    resp.headers["Vary"] = "Authorization"
    return resp


# ──────────────────────────────────────────────
#  Google OAuth routes
# ──────────────────────────────────────────────
def _get_redirect_uri():
    # Railway/proxies send X-Forwarded-Proto; use https in production
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.host
    return f"{scheme}://{host}/api/auth/google/callback"


@app.route("/api/auth/google")
@limiter.limit("10 per minute")
def auth_google():
    return_to = request.args.get("returnTo", "/")
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _get_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "hd": "husky.neu.edu",
    }
    is_popup = request.args.get("popup") == "1"
    resp = make_response(redirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}"))
    resp.set_cookie("auth_return_to", return_to, max_age=600, httponly=True, samesite="None", secure=True)
    if is_popup:
        resp.set_cookie("auth_popup", "1", max_age=600, httponly=True, samesite="None", secure=True)
    return resp


@app.route("/api/auth/google/callback")
@limiter.limit("10 per minute")
def auth_google_callback():
    code = request.args.get("code")
    if not code:
        return redirect(f"{FRONTEND_URL}?auth_error=no_code")

    try:
        token_resp = http_requests.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": _get_redirect_uri(),
            "grant_type": "authorization_code",
        }, timeout=10)
        if token_resp.status_code != 200:
            return redirect(f"{FRONTEND_URL}?auth_error=token_exchange_failed")

        access_token = token_resp.json().get("access_token")

        user_resp = http_requests.get(GOOGLE_USERINFO_URL, headers={
            "Authorization": f"Bearer {access_token}",
        }, timeout=10)
        if user_resp.status_code != 200:
            return redirect(f"{FRONTEND_URL}?auth_error=userinfo_failed")
    except Exception:
        return redirect(f"{FRONTEND_URL}?auth_error=timeout")

    user_info = user_resp.json()

    if user_info.get("hd") != "husky.neu.edu":
        return redirect(f"{FRONTEND_URL}?auth_error=invalid_domain")

    payload = {
        "sub": user_info["id"],
        "email": user_info["email"],
        "name": user_info.get("name", ""),
        "picture": user_info.get("picture", ""),
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    token = pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")

    is_popup = request.cookies.get("auth_popup") == "1"

    if is_popup:
        # Desktop popup: post message to opener and close
        resp = make_response(f"""
        <html><body><script>
          window.opener && window.opener.postMessage({{ type: "auth_complete", token: "{token}" }}, "{FRONTEND_URL}");
          window.close();
        </script></body></html>
        """)
        resp.delete_cookie("auth_popup")
    else:
        # Mobile redirect: pass token via URL fragment (not querystring, so it's not logged)
        return_to = request.cookies.get("auth_return_to", "/")
        from urllib.parse import urlparse
        parsed = urlparse(return_to)
        if parsed.scheme or parsed.netloc or not return_to.startswith("/"):
            return_to = "/"
        resp = make_response(redirect(f"{FRONTEND_URL}{return_to}#auth_token={token}"))
        resp.delete_cookie("auth_return_to")
    return resp


def _get_auth_token():
    """Get JWT token from Authorization header or cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.cookies.get("auth_token")


@app.route("/api/auth/me")
@limiter.limit("30 per minute")
def auth_me():
    token = _get_auth_token()
    if not token:
        return jsonify(None), 401

    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return jsonify({
            "email": payload["email"],
            "name": payload["name"],
            "picture": payload.get("picture", ""),
        })
    except pyjwt.ExpiredSignatureError:
        return jsonify(None), 401
    except pyjwt.InvalidTokenError:
        return jsonify(None), 401


@app.route("/api/auth/logout", methods=["POST"])
@limiter.limit("10 per minute")
def auth_logout():
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie("auth_token")
    return resp


@app.route("/api/feedback", methods=["POST"])
@limiter.limit("10 per day")
def submit_feedback():
    global _feedback_count, _feedback_date

    data = request.get_json(silent=True) or {}
    feedback_type = data.get("feedbackType", "").strip()
    description = data.get("description", "").strip()
    reply_email = data.get("email", "").strip()
    turnstile_token = data.get("turnstileToken", "").strip()

    # Verify Cloudflare Turnstile CAPTCHA
    turnstile_secret = os.getenv("TURNSTILE_SECRET_KEY")
    if turnstile_secret:
        if not turnstile_token:
            return jsonify({"error": "CAPTCHA verification required"}), 400
        try:
            verify_resp = http_requests.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": turnstile_secret, "response": turnstile_token, "remoteip": get_remote_address()},
                timeout=5,
            )
            if not verify_resp.ok or not verify_resp.json().get("success"):
                return jsonify({"error": "CAPTCHA verification failed"}), 403
        except Exception:
            return jsonify({"error": "CAPTCHA verification failed"}), 500

    if not feedback_type or not description:
        return jsonify({"error": "feedbackType and description are required"}), 400

    if reply_email and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', reply_email):
        return jsonify({"error": "Invalid email address"}), 400

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _feedback_lock:
        if _feedback_date != today:
            _feedback_date = today
            _feedback_count = 0
        if _feedback_count >= FEEDBACK_DAILY_LIMIT:
            return jsonify({"error": "Daily feedback limit reached. Please try again tomorrow."}), 429
        _feedback_count += 1

    resend_api_key = os.getenv("RESEND_API_KEY")
    if not resend_api_key:
        print("[feedback] RESEND_API_KEY not configured")
        return jsonify({"error": "Email service not configured"}), 500

    type_labels = {
        "bug": "Bug Report",
        "feature": "Feature Request",
        "missing": "Missing Data",
        "incorrectdata": "Incorrect Data",
        "general": "General Feedback",
    }
    type_label = type_labels.get(feedback_type, feedback_type)
    submitted_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        f"Submitted:   {submitted_at}",
        f"Type:        {type_label}",
    ]
    if reply_email:
        lines.append(f"From:        {reply_email}")
    lines += ["", "Description:", description]
    body = "\n".join(lines)

    payload = {
        "from": "RateMyHusky <feedback@ratemyhusky.com>",
        "to": ["feedback@ratemyhusky.com"],
        "subject": f"[RateMyHusky] {type_label}",
        "text": body,
    }
    if reply_email:
        payload["reply_to"] = reply_email

    try:
        resp = http_requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        if not resp.ok:
            print(f"[feedback] Resend error {resp.status_code}: {resp.text}")
            return jsonify({"error": "Failed to send email"}), 500
    except Exception as e:
        print(f"[feedback] Resend request error: {e}")
        return jsonify({"error": "Failed to send email"}), 500

    return jsonify({"ok": True})


@app.route("/api/trace-dept-avg")
def trace_dept_avg():
    department = request.args.get("department", "").strip()
    try:
        term_id = int(request.args.get("term_id", "0"))
    except (ValueError, TypeError):
        term_id = 0

    if not department or not term_id:
        return jsonify([])

    cache_key = f"trace_dept_avg:{department}:{term_id}"
    cached = cache_get(cache_key)
    if cached:
        resp = jsonify(cached)
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp

    rows = query("""
        SELECT ts.question,
               SUM(COALESCE(ts.count_1, 0) + COALESCE(ts.count_2, 0) + COALESCE(ts.count_3, 0)
                   + COALESCE(ts.count_4, 0) + COALESCE(ts.count_5, 0)) AS total_responses,
               SUM(1 * COALESCE(ts.count_1, 0) + 2 * COALESCE(ts.count_2, 0)
                   + 3 * COALESCE(ts.count_3, 0) + 4 * COALESCE(ts.count_4, 0)
                   + 5 * COALESCE(ts.count_5, 0)) AS weighted_sum
        FROM trace_scores ts
        JOIN trace_courses tc
            ON ts.course_id = tc.course_id
           AND ts.instructor_id = tc.instructor_id
           AND ts.term_id = tc.term_id
        WHERE tc.department_name = %s AND tc.term_id = %s
        GROUP BY ts.question
    """, (department, term_id))

    result = []
    for r in rows:
        total = int(r["total_responses"] or 0)
        wsum = float(r["weighted_sum"] or 0)
        if total > 0:
            result.append({
                "question": str(r["question"] or ""),
                "avgMean": round(wsum / total, 2),
            })

    # Fallback: if count columns are unpopulated for this term, use mean directly
    if not result:
        rows = query("""
            SELECT ts.question,
                   SUM(COALESCE(ts.mean, 0) * COALESCE(ts.completed, 1)::FLOAT) AS weighted_sum,
                   SUM(COALESCE(ts.completed, 1))::FLOAT AS total_weight
            FROM trace_scores ts
            JOIN trace_courses tc
                ON ts.course_id = tc.course_id
               AND ts.instructor_id = tc.instructor_id
               AND ts.term_id = tc.term_id
            WHERE tc.department_name = %s AND tc.term_id = %s AND ts.mean IS NOT NULL
            GROUP BY ts.question
        """, (department, term_id))
        for r in rows:
            total_weight = float(r["total_weight"] or 0)
            wsum = float(r["weighted_sum"] or 0)
            if total_weight > 0:
                result.append({
                    "question": str(r["question"] or ""),
                    "avgMean": round(wsum / total_weight, 2),
                })

    cache_set(cache_key, result)
    resp = jsonify(result)
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


if __name__ == "__main__":
    print("Starting server on port 5001...")
    app.run(debug=True, port=5001, use_reloader=True)
