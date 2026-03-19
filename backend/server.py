"""
Backend API server for NEU Professor Ratings.
No pandas/numpy — queries CockroachDB directly per request.

Install deps:  pip install flask flask-cors flask-limiter psycopg2-binary pyjwt requests python-dotenv gunicorn
Run:           python server.py
"""

import os, re, unicodedata, json, hashlib
import html as _html
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
from functools import lru_cache
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, redirect, make_response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import jwt as pyjwt
import requests as http_requests
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from threading import Lock
import time

load_dotenv()


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────
def normalize_name(name):
    s = str(name).strip().lower()
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def sanitize(text: str) -> str:
    return _html.escape(str(text), quote=False)


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
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
CORS(app, supports_credentials=True, origins=[FRONTEND_URL])
limiter = Limiter(get_remote_address, app=app, default_limits=["120 per minute"])


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

pool = SimpleConnectionPool(1, 5, CRDB_DATABASE_URL, sslmode="require")

# ──────────────────────────────────────────────
#  Simple in-memory cache (TTL-based)
# ──────────────────────────────────────────────
_cache = {}
_cache_lock = Lock()
CACHE_TTL = 300  # 5 minutes


def cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < CACHE_TTL:
            return entry["data"]
        return None


def cache_set(key, data):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": time.time()}
        # Evict old entries if cache gets too large
        if len(_cache) > 2000:
            cutoff = time.time() - CACHE_TTL
            expired = [k for k, v in _cache.items() if v["ts"] < cutoff]
            for k in expired:
                del _cache[k]


def get_db():
    if 'db' not in g:
        g.db = pool.getconn()
    return g.db


@app.teardown_appcontext
def return_db(exc):
    db = g.pop('db', None)
    if db is not None:
        pool.putconn(db)


def query(sql, params=None):
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
    words = q.split()
    if not words:
        return []

    # Build WHERE: each word must appear somewhere in name_key
    conditions = []
    params = []
    for word in words:
        conditions.append("name_key LIKE %s")
        params.append(f"%{word}%")

    where = " AND ".join(conditions)
    rows = query(
        f"SELECT slug, name, name_key, department, avg_rating, total_reviews "
        f"FROM professors_catalog WHERE {where} "
        f"ORDER BY total_reviews DESC LIMIT 100",
        params
    )

    # Rank in Python for proper tiered relevance
    def rank_match(row):
        nk = row['name_key']
        parts = nk.split()

        # Tier 1: q matches a whole name part exactly
        if q in parts:
            return 1
        # Tier 2: q matches the start of any name part
        if any(p.startswith(q) for p in parts):
            return 2
        # Tier 3: q is a substring of the full name
        if q in nk:
            return 3
        # Tier 4: multi-word: each word prefixes a name part
        if len(words) >= 2 and all(any(p.startswith(w) for p in parts) for w in words):
            return 4
        # Tier 5: multi-word: each word is substring of any name part
        if len(words) >= 2 and all(any(w in p for p in parts) for w in words):
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
    rows = query("""
        SELECT college, COUNT(*) as cnt FROM professors_catalog
        WHERE avg_rating IS NOT NULL
        GROUP BY college HAVING COUNT(*) >= 5
        ORDER BY college
    """)
    return jsonify([r['college'] for r in rows if r['college'] != 'Other'])


NO_MIN_COLLEGES = {"Law", "Professional Studies"}


@app.route("/api/goat-professors")
def goat_professors():
    college = request.args.get("college", "Khoury")
    limit = min(int(request.args.get("limit", "10")), 50)
    min_reviews = int(request.args.get("min_reviews", "10"))

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
            WHERE college = %s AND num_ratings >= %s AND trace_reviews >= %s AND trace_rating IS NOT NULL
            ORDER BY avg_rating DESC NULLS LAST, total_reviews DESC
            LIMIT %s
        """, (college, min_reviews, min_reviews, limit))

    result = []
    for row in rows:
        result.append({
            "name": row["name"],
            "dept": row["department"],
            "rmpRating": round(row["rmp_rating"], 2) if row["rmp_rating"] else None,
            "traceRating": round(row["trace_rating"], 2) if row["trace_rating"] else None,
            "avgRating": round(row["avg_rating"], 2) if row["avg_rating"] else None,
            "rmpReviews": row["num_ratings"],
            "traceReviews": row["trace_reviews"],
            "totalReviews": row["total_reviews"],
            "url": row["professor_url"] or "",
        })
    return jsonify(result)


@app.route("/api/random-professor")
def random_professor():
    row = query_one("""
        SELECT * FROM professors_catalog
        WHERE num_ratings >= 3
        ORDER BY random() LIMIT 1
    """)
    if not row:
        return jsonify({"error": "No professors found"}), 404
    return jsonify({
        "name": row["name"],
        "dept": row["department"],
        "rmpRating": round(row["rmp_rating"], 2) if row["rmp_rating"] else None,
        "traceRating": round(row["trace_rating"], 2) if row["trace_rating"] else None,
        "avgRating": round(row["avg_rating"], 2) if row["avg_rating"] else None,
        "rmpReviews": row["num_ratings"],
        "traceReviews": row["trace_reviews"],
        "totalReviews": row["total_reviews"],
        "url": row["professor_url"] or "",
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
#  Professor profile page
# ──────────────────────────────────────────────
@app.route("/api/professors/<slug>")
def professor_profile(slug):
    # Validate auth upfront for cache separation
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
        resp.headers["Cache-Control"] = "private, max-age=300" if is_authed else "public, max-age=300"
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
        "numRatings": prof["num_ratings"],
        "wouldTakeAgainPct": round(prof["would_take_again_pct"], 1) if prof["would_take_again_pct"] else None,
        "difficulty": round(prof["difficulty"], 2) if prof["difficulty"] else None,
        "totalRatings": prof["total_reviews"],
        "professorUrl": prof["professor_url"],
        "imageUrl": prof["image_url"],
    }

    # ── TRACE courses + scores (batched into 2 queries instead of N+1) ──
    trace_course_rows = query("""
        SELECT course_id, term_id, term_title, department_name, display_name,
               section, enrollment, instructor_id
        FROM trace_courses WHERE name_key = %s
        ORDER BY term_id DESC
    """, (name_key,))

    # Batch-fetch all scores for this professor's courses in one query
    scores_by_key = {}
    if trace_course_rows:
        keys = [(int(c["course_id"]), int(c["instructor_id"]), int(c["term_id"] or 0)) for c in trace_course_rows]
        or_clauses = []
        score_params = []
        for cid, iid, tid in keys:
            or_clauses.append("(course_id = %s AND instructor_id = %s AND term_id = %s)")
            score_params.extend([cid, iid, tid])

        all_scores = query(
            f"SELECT course_id, instructor_id, term_id, question, mean, median, std_dev, "
            f"enrollment, completed, total_responses, count_1, count_2, count_3, count_4, count_5 "
            f"FROM trace_scores WHERE {' OR '.join(or_clauses)}",
            score_params
        )
        for s in all_scores:
            k = (int(s["course_id"]), int(s["instructor_id"]), int(s["term_id"] or 0))
            scores_by_key.setdefault(k, []).append(s)

    trace_course_list = []
    for c in trace_course_rows:
        cid = int(c["course_id"])
        iid = int(c["instructor_id"])
        tid = int(c["term_id"]) if c["term_id"] else 0

        scores_list = []
        for s in scores_by_key.get((cid, iid, tid), []):
            scores_list.append({
                "question": str(s["question"] or ""),
                "mean": round(float(s["mean"]), 2) if s["mean"] else 0,
                "median": round(float(s["median"]), 2) if s["median"] else 0,
                "stdDev": round(float(s["std_dev"]), 2) if s["std_dev"] else 0,
                "enrollment": int(s["enrollment"] or 0),
                "completed": int(s["completed"] or 0),
                "totalResponses": int(s["total_responses"] or 0),
                "count1": int(s["count_1"] or 0),
                "count2": int(s["count_2"] or 0),
                "count3": int(s["count_3"] or 0),
                "count4": int(s["count_4"] or 0),
                "count5": int(s["count_5"] or 0),
            })

        trace_course_list.append({
            "courseId": cid,
            "termId": tid,
            "termTitle": str(c["term_title"] or ""),
            "departmentName": str(c["department_name"] or ""),
            "displayName": str(c["display_name"] or ""),
            "section": str(c["section"] or ""),
            "enrollment": int(c["enrollment"] or 0),
            "scores": scores_list,
        })

    profile["traceCourses"] = trace_course_list

    # ── RMP reviews ──
    review_rows = query("""
        SELECT professor_name, department, overall_rating, course,
               quality, difficulty, date, tags, attendance, grade,
               textbook, online_class, comment
        FROM rmp_reviews WHERE name_key = %s
    """, (name_key,))

    reviews = []
    for r in review_rows:
        reviews.append({
            "professorName": str(r["professor_name"] or ""),
            "department": str(r["department"] or ""),
            "overallRating": float(r["overall_rating"]) if r["overall_rating"] else 0,
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
    profile["reviews"] = reviews

    # ── TRACE comments (auth required) ──
    if is_authed and trace_course_rows:
        url_patterns = set()
        for c in trace_course_rows:
            cid = str(int(c["course_id"]))
            iid = str(int(c["instructor_id"]))
            tid = str(int(c["term_id"])) if c["term_id"] else ""
            url_patterns.add(f"sp={cid}&sp={iid}&sp={tid}")

        # Build OR conditions for URL matching
        or_conditions = []
        or_params = []
        for pat in url_patterns:
            or_conditions.append("course_url LIKE %s")
            or_params.append(f"%{pat}%")

        if or_conditions:
            comment_rows = query(
                f"SELECT course_url, question, comment FROM trace_comments WHERE {' OR '.join(or_conditions)}",
                or_params
            )

            comments = []
            for c in comment_rows:
                comment_text = sanitize(c["comment"]) if c["comment"] else ""
                if not comment_text.strip():
                    continue

                url = str(c["course_url"] or "")
                term_id = 0
                try:
                    sp_matches = re.findall(r"sp=(\d+)", url)
                    if len(sp_matches) >= 3:
                        term_id = int(sp_matches[2])
                except (ValueError, IndexError):
                    pass

                comments.append({
                    "courseUrl": url,
                    "question": str(c["question"] or ""),
                    "comment": comment_text,
                    "termId": term_id,
                })
            profile["traceComments"] = comments
        else:
            profile["traceComments"] = []
    else:
        profile["traceComments"] = []

    cache_set(cache_key, profile)
    resp = jsonify(profile)
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@app.route("/api/departments")
def departments():
    college = request.args.get("college", "")
    if college and college != "All":
        rows = query("""
            SELECT DISTINCT department FROM professors_catalog
            WHERE avg_rating IS NOT NULL AND college = %s
            ORDER BY department
        """, (college,))
    else:
        rows = query("""
            SELECT DISTINCT department FROM professors_catalog
            WHERE avg_rating IS NOT NULL
            ORDER BY department
        """)
    return jsonify([r['department'] for r in rows if r['department']])


@app.route("/api/professors-catalog")
def professors_catalog():
    q = normalize_name(request.args.get("q", ""))
    college = request.args.get("college", "")
    dept_list = [d.strip() for d in request.args.getlist("dept") if d.strip()]
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
        conditions.append("college = %s")
        params.append(college)
    if dept_list:
        if len(dept_list) == 1:
            conditions.append("department = %s")
            params.append(dept_list[0])
        else:
            placeholders = ','.join(['%s'] * len(dept_list))
            conditions.append(f"department IN ({placeholders})")
            params.extend(dept_list)
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
        elif sort == "reviews":
            order = "total_reviews DESC"
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
            "wouldTakeAgainPct": round(row["would_take_again_pct"], 1) if row["would_take_again_pct"] else None,
            "imageUrl": row["image_url"],
        })

    return jsonify({
        "professors": professors,
        "total": total,
        "page": page,
        "totalPages": total_pages,
    })


@app.route("/api/course-departments")
def course_departments():
    rows = query("""
        SELECT DISTINCT department FROM course_catalog
        WHERE department IS NOT NULL AND department != ''
        ORDER BY department
    """)
    return jsonify([r["department"] for r in rows])


@app.route("/api/courses-catalog")
def courses_catalog():
    q = normalize_name(request.args.get("q", ""))
    dept_list = [d.strip() for d in request.args.getlist("dept") if d.strip()]
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

    # Build the base query using course_catalog + trace aggregation
    # course_catalog has: code, name, department, search_text
    # We need to join with trace data for ratings/sections/enrollment
    where_clauses = []
    params = []

    if dept_list:
        if len(dept_list) == 1:
            where_clauses.append("cc.department = %s")
            params.append(dept_list[0])
        else:
            placeholders = ','.join(['%s'] * len(dept_list))
            where_clauses.append(f"cc.department IN ({placeholders})")
            params.extend(dept_list)
    if q:
        where_clauses.append("cc.search_text LIKE %s")
        params.append(f"%{q}%")

    where_sql = (" AND " + " AND ".join(where_clauses)) if where_clauses else ""

    # Aggregate course stats from trace_courses + trace_scores
    catalog_sql = f"""
        WITH course_sections AS (
            SELECT DISTINCT ON (tc.course_id, tc.instructor_id, tc.term_id)
                tc.course_id, tc.instructor_id, tc.term_id, tc.term_title,
                tc.department_name, tc.display_name, tc.enrollment
            FROM trace_courses tc
            WHERE tc.display_name IS NOT NULL
        ),
        overall_scores AS (
            SELECT ts.course_id, ts.instructor_id, ts.term_id,
                   SUM(ts.mean * ts.total_responses) as weighted_sum,
                   SUM(ts.total_responses) as total_responses
            FROM trace_scores ts
            WHERE lower(ts.question) LIKE '%%overall%%'
            GROUP BY ts.course_id, ts.instructor_id, ts.term_id
        ),
        course_agg AS (
            SELECT cc.code, cc.name, cc.department,
                   COUNT(cs.course_id) as total_sections,
                   COUNT(DISTINCT cs.instructor_id) as total_instructors,
                   COALESCE(SUM(cs.enrollment), 0) as total_enrollment,
                   COALESCE(SUM(os.total_responses), 0) as total_responses,
                   CASE WHEN SUM(os.total_responses) > 0
                        THEN SUM(os.weighted_sum) / SUM(os.total_responses)
                        ELSE NULL END as avg_rating,
                   MAX(cs.term_id) as latest_term_id,
                   MAX(cs.term_title) as latest_term_title
            FROM course_catalog cc
            JOIN trace_courses tc ON upper(regexp_replace(
                split_part(tc.display_name, ':', 1), '[^A-Za-z0-9]', '', 'g'
            )) = cc.code
            JOIN course_sections cs ON cs.course_id = tc.course_id
                AND cs.instructor_id = tc.instructor_id AND cs.term_id = tc.term_id
            LEFT JOIN overall_scores os ON os.course_id = cs.course_id
                AND os.instructor_id = cs.instructor_id AND os.term_id = cs.term_id
            WHERE 1=1 {where_sql}
            GROUP BY cc.code, cc.name, cc.department
            HAVING CASE WHEN SUM(os.total_responses) > 0
                        THEN SUM(os.weighted_sum) / SUM(os.total_responses)
                        ELSE NULL END IS NOT NULL
        )
        SELECT * FROM course_agg
        {"WHERE avg_rating >= %s AND avg_rating <= %s" if min_rating > 0 or max_rating < 5 else ""}
    """

    if min_rating > 0 or max_rating < 5:
        params.extend([min_rating, max_rating])

    # This is complex — use a simpler approach: query course_catalog directly
    # and do a second query for aggregates
    # Simplified approach: use course_catalog for listing, compute stats per-request
    count_where = []
    count_params = []

    if dept_list:
        if len(dept_list) == 1:
            count_where.append("department = %s")
            count_params.append(dept_list[0])
        else:
            placeholders = ','.join(['%s'] * len(dept_list))
            count_where.append(f"department IN ({placeholders})")
            count_params.extend(dept_list)
    if q:
        count_where.append("search_text LIKE %s")
        count_params.append(f"%{q}%")

    where_str = ("WHERE " + " AND ".join(count_where)) if count_where else ""

    count_row = query_one(f"SELECT COUNT(*) as cnt FROM course_catalog {where_str}", count_params)
    total = count_row["cnt"] if count_row else 0

    total_pages = max(1, (total + limit - 1) // limit)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * limit

    sort_map = {
        "rating": "code ASC",  # Will sort in Python after getting ratings
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

    courses = []
    for r in rows:
        courses.append({
            "code": r["code"],
            "name": r["name"],
            "department": r["department"],
            "avgRating": None,
            "totalSections": 0,
            "totalInstructors": 0,
            "totalEnrollment": 0,
            "totalResponses": 0,
            "latestTermTitle": "",
            "latestTermId": 0,
        })

    return jsonify({
        "courses": courses,
        "total": total,
        "page": page,
        "totalPages": total_pages,
    })


@app.route("/api/courses/<code>")
def course_profile(code):
    code_norm = _format_course_code(code)
    if not code_norm:
        return jsonify({"error": "Course not found"}), 404

    # Look up course in catalog
    course = query_one("SELECT code, name, department FROM course_catalog WHERE code = %s", (code_norm,))
    if not course:
        return jsonify({"error": "Course not found"}), 404

    # Parse course code pattern to match trace_courses display_name
    # display_name format: "CS2500:01 (Fundamentals ...)"
    code_pattern = f"{code_norm}:%"

    # Get all sections for this course from trace_courses
    sections = query("""
        SELECT DISTINCT ON (tc.course_id, tc.instructor_id, tc.term_id)
            tc.course_id, tc.instructor_id, tc.term_id, tc.term_title,
            tc.department_name, tc.display_name, tc.section, tc.enrollment,
            tc.instructor_first_name, tc.instructor_last_name
        FROM trace_courses tc
        WHERE tc.display_name LIKE %s
        ORDER BY tc.course_id, tc.instructor_id, tc.term_id, tc.term_id DESC
    """, (code_pattern,))

    if not sections:
        return jsonify({"error": "Course not found"}), 404

    # Get overall scores for these sections
    if sections:
        or_clauses = []
        score_params = []
        for s in sections:
            or_clauses.append("(course_id = %s AND instructor_id = %s AND term_id = %s)")
            score_params.extend([s["course_id"], s["instructor_id"], s["term_id"]])

        overall_scores = query(
            f"SELECT course_id, instructor_id, term_id, "
            f"SUM(CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT)) as weighted_sum, "
            f"SUM(total_responses) as total_responses, "
            f"SUM(completed) as completed "
            f"FROM trace_scores "
            f"WHERE ({' OR '.join(or_clauses)}) AND lower(question) LIKE '%%overall%%' "
            f"GROUP BY course_id, instructor_id, term_id",
            score_params
        )
    else:
        overall_scores = []

    # Build score lookup
    score_map = {}
    for os_row in overall_scores:
        key = (os_row["course_id"], os_row["instructor_id"], os_row["term_id"])
        score_map[key] = os_row

    # Compute summary
    total_weighted = 0.0
    total_responses = 0
    total_enrollment = 0
    instructor_ids = set()
    latest_term_id = 0
    latest_term_title = ""

    for s in sections:
        total_enrollment += _safe_int(s["enrollment"])
        instructor_ids.add(s["instructor_id"])
        tid = _safe_int(s["term_id"])
        if tid > latest_term_id:
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
        "totalSections": len(sections),
        "totalInstructors": len(instructor_ids),
        "totalEnrollment": total_enrollment,
        "totalResponses": total_responses,
        "latestTermTitle": latest_term_title,
        "latestTermId": latest_term_id,
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
            instructor_data[name] = {"sections": 0, "enrollment": 0, "weighted": 0.0, "responses": 0}
        instructor_data[name]["sections"] += 1
        instructor_data[name]["enrollment"] += _safe_int(s["enrollment"])
        key = (s["course_id"], s["instructor_id"], s["term_id"])
        if key in score_map:
            instructor_data[name]["weighted"] += _safe_float(score_map[key]["weighted_sum"])
            instructor_data[name]["responses"] += _safe_int(score_map[key]["total_responses"])

    # Look up instructor metadata from professors_catalog
    instructor_rows = []
    for name, data in instructor_data.items():
        name_key = normalize_name(name)
        prof = query_one(
            "SELECT slug, image_url, total_reviews, would_take_again_pct, difficulty "
            "FROM professors_catalog WHERE name_key = %s", (name_key,)
        )
        meta_slug = prof["slug"] if prof else ""
        meta_image = prof["image_url"] if prof else None
        meta_reviews = prof["total_reviews"] if prof else 0
        meta_wta = round(prof["would_take_again_pct"], 1) if prof and prof["would_take_again_pct"] else None
        meta_diff = round(prof["difficulty"], 2) if prof and prof["difficulty"] else None

        resp = data["responses"]
        instructor_rows.append({
            "name": name,
            "slug": meta_slug,
            "imageUrl": meta_image,
            "difficulty": meta_diff,
            "wouldTakeAgainPct": meta_wta,
            "totalReviews": meta_reviews or 0,
            "sections": data["sections"],
            "totalEnrollment": data["enrollment"],
            "totalResponses": resp,
            "avgRating": round(data["weighted"] / resp, 2) if resp > 0 else None,
        })
    instructor_rows.sort(key=lambda r: (r["avgRating"] is None, -(r["avgRating"] or 0), -r["sections"]))

    # Build section rows
    section_rows = []
    for s in sorted(sections, key=lambda x: -(x["term_id"] or 0)):
        key = (s["course_id"], s["instructor_id"], s["term_id"])
        sc = score_map.get(key)
        fname = (s["instructor_first_name"] or "").strip()
        lname = (s["instructor_last_name"] or "").strip()
        overall_mean = None
        if sc and _safe_int(sc["total_responses"]) > 0:
            overall_mean = round(_safe_float(sc["weighted_sum"]) / _safe_int(sc["total_responses"]), 2)
        section_rows.append({
            "courseId": _safe_int(s["course_id"]),
            "instructorId": _safe_int(s["instructor_id"]),
            "termId": _safe_int(s["term_id"]),
            "termTitle": s["term_title"] or "",
            "section": s["section"] or "",
            "instructor": f"{fname} {lname}".strip(),
            "enrollment": _safe_int(s["enrollment"]),
            "overallRating": overall_mean,
            "totalResponses": _safe_int(sc["total_responses"]) if sc else 0,
            "completed": _safe_int(sc["completed"]) if sc else 0,
        })

    # Get question-level scores
    question_rows = []
    if sections:
        or_clauses = []
        q_params = []
        for s in sections:
            or_clauses.append("(course_id = %s AND instructor_id = %s AND term_id = %s)")
            q_params.extend([s["course_id"], s["instructor_id"], s["term_id"]])

        q_scores = query(
            f"SELECT question, "
            f"SUM(CAST(mean AS FLOAT) * CAST(total_responses AS FLOAT)) as weighted_sum, "
            f"SUM(total_responses) as total_responses "
            f"FROM trace_scores "
            f"WHERE {' OR '.join(or_clauses)} "
            f"GROUP BY question",
            q_params
        )
        for qs in q_scores:
            resp = _safe_int(qs["total_responses"])
            question_rows.append({
                "question": qs["question"],
                "avgRating": round(_safe_float(qs["weighted_sum"]) / resp, 2) if resp > 0 else None,
                "totalResponses": resp,
            })
    question_rows.sort(key=lambda r: (-r["totalResponses"], r["question"].lower()))

    return jsonify({
        "summary": summary,
        "instructors": instructor_rows,
        "sections": section_rows,
        "questionScores": question_rows,
    })


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

    token_resp = http_requests.post(GOOGLE_TOKEN_URL, data={
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": _get_redirect_uri(),
        "grant_type": "authorization_code",
    })
    if token_resp.status_code != 200:
        return redirect(f"{FRONTEND_URL}?auth_error=token_exchange_failed")

    access_token = token_resp.json().get("access_token")

    user_resp = http_requests.get(GOOGLE_USERINFO_URL, headers={
        "Authorization": f"Bearer {access_token}",
    })
    if user_resp.status_code != 200:
        return redirect(f"{FRONTEND_URL}?auth_error=userinfo_failed")

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


if __name__ == "__main__":
    print("Starting server on port 5001...")
    app.run(debug=True, port=5001, use_reloader=True)
