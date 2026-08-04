"""
Build the unified `evidence` corpus (RMP + TRACE + Reddit) in CockroachDB for Ask retrieval.
Idempotent. Reads existing source tables; writes only `evidence` + `evidence_embeddings` schema.

Usage:
    python load_evidence_to_crdb.py --selftest        # offline checks, exit
    python load_evidence_to_crdb.py --build-evidence  # populate evidence (Task 2)
"""
import argparse, itertools, os, sys, time, re, hashlib, unicodedata
from dotenv import load_dotenv
import psycopg2

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend", ".env"))
URL = os.getenv("NEW_CRDB_DATABASE_URL") or os.getenv("CRDB_DATABASE_URL")

_counter = itertools.count()

EVIDENCE_DDL = """
CREATE TABLE IF NOT EXISTS evidence (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source        TEXT NOT NULL,
    source_ref    TEXT NOT NULL,
    professor_slug TEXT,
    -- empty-string sentinel, never NULL (see dedupe note below)
    course_code   TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL,
    body_tsv      TSVECTOR,
    body_sha      TEXT NOT NULL,
    sentiment     TEXT,
    reddit_score  INT,
    permalink     TEXT,
    rmp_meta      JSONB,
    flagged       BOOLEAN DEFAULT false,
    subreddit     TEXT,
    created_utc   TIMESTAMPTZ,
    -- CockroachDB rejects an expression (COALESCE(...)) in an ON CONFLICT target, so the dedupe
    -- key is a PLAIN 4-column unique index and course_code is an empty-string sentinel (never
    -- NULL) — a professor-only row is course_code='' and still dedupes, and
    -- ON CONFLICT (source, source_ref, professor_slug, course_code) parses on CRDB.
    UNIQUE (source, source_ref, professor_slug, course_code)
);
CREATE INDEX IF NOT EXISTS ev_tsv    ON evidence USING GIN (body_tsv);
CREATE INDEX IF NOT EXISTS ev_prof   ON evidence (professor_slug);
CREATE INDEX IF NOT EXISTS ev_course ON evidence (course_code);

CREATE TABLE IF NOT EXISTS evidence_embeddings (
    evidence_id   UUID PRIMARY KEY REFERENCES evidence(id) ON DELETE CASCADE,
    embedding     VECTOR(384) NOT NULL,
    model_version TEXT NOT NULL,
    body_sha      TEXT,
    embedded_at   TIMESTAMPTZ DEFAULT now()
);
"""
# NO vector index on evidence_embeddings — deliberate. Retrieval is entity-scoped (filter by
# ev_prof/ev_course, then brute-force cosine over that entity's rows; EXPLAIN-verified lookup
# join), so a global C-SPANN index buys nothing AND is a write-contention hotspot: its backfill
# serialization-killed both the embedding backfill (2026-06-30) and a --build-evidence run
# (2026-07-02, index accidentally re-created by this DDL after being dropped live). Recreate
# only if a future EXPLAIN shows a query that actually needs it.

def all_ddl():
    return EVIDENCE_DDL

def connect(attempts=20):
    last = None
    for i in range(1, attempts + 1):
        try:
            return psycopg2.connect(URL, sslmode="require")
        except psycopg2.OperationalError as e:
            if "could not translate host name" not in str(e):
                raise
            last = str(e)
            print(f"  DNS lookup flaked; retrying ({i}/{attempts})...")
            time.sleep(1.5)
    raise SystemExit(f"Could not connect after {attempts} attempts: {last}")

def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(EVIDENCE_DDL)
    conn.commit()

# ── Sanitization + injection screening (mirrored from load_reddit_to_crdb.py) ──

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)

_INJECTION_PATTERNS = [
    (re.compile(r"ignore (all |the |any |previous )?(instructions|rules|prompts?)", re.I), "ignore_instructions"),
    (re.compile(r"you are now", re.I), "persona_switch"),
    (re.compile(r"</?(system|user|assistant|instructions?)\s*>", re.I), "role_tag"),
    (re.compile(r"<\|.*?\|>"), "chatml_token"),
    (re.compile(r"(disregard|override|bypass) (all|the|any|your|previous|the above)", re.I), "override"),
]

def sanitize_body(text: str) -> str:
    """NFKC-normalize, strip zero-width/control chars, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ZERO_WIDTH)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return re.sub(r"\s+", " ", text).strip()

def injection_flag(text: str) -> "tuple[bool, str | None]":
    """Return (True, reason) if text matches an injection pattern, else (False, None)."""
    for pat, reason in _INJECTION_PATTERNS:
        if pat.search(text or ""):
            return True, reason
    return False, None

# ── Pure helper functions ──

_NA = {"", "n/a", "na", "none", "no", "-", ".", "nothing", "no comment", "no comments"}

def norm_code(s: str) -> str:
    """Uppercase and strip all whitespace from a course code string."""
    return re.sub(r"\s+", "", str(s or "").upper())

def body_sha(text: str) -> str:
    """SHA-256 hex digest (first 32 chars) of text."""
    return hashlib.sha256((text or "").encode()).hexdigest()[:32]

def is_meaningful(text: str) -> bool:
    """Return True if text passes TRACE filter: non-empty, not n/a, >=15 chars."""
    t = sanitize_body(text)
    if not t or t.lower() in _NA:
        return False
    return len(t.strip()) >= 15

def dedup_key(text: str) -> str:
    """Normalized 80-char prefix for deduplication (matches professor_full._dedup_group)."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()[:80]

def _row(source, source_ref, slug, code, body, sentiment=None, score=None,
         permalink=None, subreddit=None, created_utc=None, rmp_meta=None):
    """Build an evidence-row dict."""
    clean = sanitize_body(body)
    flagged, _reason = injection_flag(clean)
    return {
        "source": source,
        "source_ref": str(source_ref),
        "professor_slug": slug,
        # empty-string sentinel, never NULL — the dedupe unique index is plain 4-column
        # (CRDB rejects an expression conflict target), so a course-less row is '' not None.
        "course_code": code or "",
        "body": clean,
        "body_sha": body_sha(clean),
        "sentiment": sentiment,
        "reddit_score": score,
        "permalink": permalink,
        "subreddit": subreddit,
        "created_utc": created_utc,
        "rmp_meta": rmp_meta,
        "flagged": flagged,
    }

# ── Build functions (pure — take injected query_fn, no live DB) ──

def build_reddit_rows(query_fn) -> list:
    """Yield evidence rows from reddit_mentions + reddit_text + reddit_sentiment."""
    rows = query_fn("""
        SELECT m.source_id, m.professor_slug, t.body, t.subreddit, t.created_utc, t.score, t.permalink,
               s.sentiment, t.flagged
        FROM reddit_mentions m
        JOIN reddit_text t ON t.source_id = m.source_id
        LEFT JOIN reddit_sentiment s
          ON s.source_id = t.source_id AND s.professor_slug = m.professor_slug
    """, ())
    out = []
    for r in rows:
        if not is_meaningful(r.get("body")):
            continue
        ev = _row("reddit", r["source_id"], r.get("professor_slug"), None, r.get("body"),
                  sentiment=r.get("sentiment"), score=r.get("score"), permalink=r.get("permalink"),
                  subreddit=r.get("subreddit"), created_utc=r.get("created_utc"))
        ev["flagged"] = bool(r.get("flagged")) or ev["flagged"]
        out.append(ev)
    return out

def build_rmp_rows(query_fn) -> list:
    """Yield evidence rows from rmp_reviews, resolving course_code via TRACE validation."""
    # trace_name_key exists because finalize.py always runs precompute.py (which
    # rebuilds this table) immediately before this step.
    catalog = query_fn("SELECT name_key, slug, trace_name_key FROM professors_catalog", ())
    slug_by_key = {r["name_key"]: r["slug"] for r in catalog}
    # RMP name_key -> TRACE name_key. A fuzzy-matched professor's TRACE sections
    # are filed under a different name than their RMP reviews, so the course-code
    # validation below has to cross over; precompute records which name to use.
    trace_key_by_key = {r["name_key"]: (r.get("trace_name_key") or r["name_key"])
                        for r in catalog}
    taught = {}  # TRACE name_key -> set(course_code) from TRACE
    for r in query_fn("SELECT DISTINCT name_key, course_code FROM trace_courses", ()):
        nk = r.get("name_key")
        code = norm_code(r.get("course_code", ""))
        if nk:
            taught.setdefault(nk, set()).add(code)
    rows = query_fn("""SELECT id, name_key, course, comment, quality, difficulty, tags, grade
                       FROM rmp_reviews WHERE comment IS NOT NULL AND comment <> ''""", ())
    out = []
    for r in rows:
        if not is_meaningful(r.get("comment")):
            continue
        nk = r.get("name_key")
        slug = slug_by_key.get(nk)
        if not slug:
            continue
        code = norm_code(r.get("course"))
        prof_codes = taught.get(trace_key_by_key.get(nk, nk), set())
        course_code = code if code in prof_codes else None
        meta = {
            "course": r.get("course"),
            "quality": r.get("quality"),
            "difficulty": r.get("difficulty"),
            "tags": r.get("tags"),
            "grade": r.get("grade"),
        }
        out.append(_row("rmp", r["id"], slug, course_code, r.get("comment"), rmp_meta=meta))
    return out

def build_trace_rows(query_fn) -> list:
    """Yield evidence rows from trace_comments joined to trace_courses + professors_catalog.

    Joins on the professor's TRACE-side name. A fuzzy-matched professor's catalog
    row is keyed by the RMP spelling while their TRACE sections use another, so
    joining on name_key alone dropped their TRACE comments from RAG citations
    entirely — an INNER JOIN, so the loss was silent. COALESCE keeps exact matches
    (trace_name_key IS NULL) working unchanged.
    """
    rows = query_fn("""
        SELECT tc.id, tc.comment, c.name_key, c.course_code,
               p.slug AS professor_slug
        FROM trace_comments tc
        JOIN trace_courses c
          ON tc.tc_course_id = c.course_id AND tc.tc_instructor_id = c.instructor_id
         AND tc.tc_term_id = c.term_id
        JOIN professors_catalog p ON COALESCE(p.trace_name_key, p.name_key) = c.name_key
        WHERE tc.comment IS NOT NULL AND tc.comment <> ''
        ORDER BY tc.id
    """, ())
    seen, out = set(), []
    for r in rows:
        if not is_meaningful(r.get("comment")):
            continue
        slug = r.get("professor_slug")
        code = norm_code(r.get("course_code"))
        k = (slug, code, dedup_key(r.get("comment")))
        if k in seen:
            continue
        seen.add(k)
        out.append(_row("trace", r["id"], slug, code or None, r.get("comment")))
    return out

# ── Upsert ──

def upsert_evidence(conn, rows, batch=1000):
    """Insert evidence rows with ON CONFLICT DO UPDATE; computes body_tsv via SQL."""
    from psycopg2.extras import execute_values
    import json

    def _jsonb(v):
        return json.dumps(v) if v is not None else None

    sql = """
        INSERT INTO evidence
          (source, source_ref, professor_slug, course_code, body, body_tsv, body_sha,
           sentiment, reddit_score, permalink, subreddit, created_utc, rmp_meta, flagged)
        VALUES %s
        ON CONFLICT (source, source_ref, professor_slug, course_code)
        DO UPDATE SET
          body         = excluded.body,
          body_tsv     = excluded.body_tsv,
          body_sha     = excluded.body_sha,
          flagged      = excluded.flagged,
          sentiment    = excluded.sentiment,
          reddit_score = excluded.reddit_score,
          permalink    = excluded.permalink,
          created_utc  = excluded.created_utc,
          rmp_meta     = excluded.rmp_meta,
          subreddit    = excluded.subreddit
    """
    template = "(%(source)s, %(source_ref)s, %(professor_slug)s, %(course_code)s, %(body)s, to_tsvector('english', %(tsv_src)s), %(body_sha)s, %(sentiment)s, %(reddit_score)s, %(permalink)s, %(subreddit)s, %(created_utc)s, %(rmp_meta)s, %(flagged)s)"

    total = 0
    for start in range(0, len(rows), batch):
        chunk = rows[start:start + batch]
        values = []
        for r in chunk:
            values.append({
                "source": r["source"],
                "source_ref": r["source_ref"],
                "professor_slug": r.get("professor_slug"),
                "course_code": r.get("course_code"),
                "body": r["body"],
                "tsv_src": r["body"],
                "body_sha": r["body_sha"],
                "sentiment": r.get("sentiment"),
                "reddit_score": r.get("reddit_score"),
                "permalink": r.get("permalink"),
                "subreddit": r.get("subreddit"),
                "created_utc": r.get("created_utc"),
                "rmp_meta": _jsonb(r.get("rmp_meta")),
                "flagged": bool(r.get("flagged")),
            })
        # CockroachDB uses SERIALIZABLE isolation and asks clients to retry transient
        # SerializationFailure (40001) with backoff. Each batch commits independently, so a
        # retried batch is safe (the ON CONFLICT upsert is idempotent).
        for attempt in range(6):
            try:
                with conn.cursor() as cur:
                    execute_values(cur, sql, values, template=template)
                conn.commit()
                break
            except psycopg2.errors.SerializationFailure:
                conn.rollback()
                if attempt == 5:
                    raise
                time.sleep(0.5 * (2 ** attempt))
        total += len(chunk)
    return total

def prune_evidence(conn, source, fresh_refs, batch=1000):
    """Delete evidence rows (and their embeddings) for `source` whose source_ref is not in
    fresh_refs — cleans up rows orphaned by a Reddit re-score or FP-mention purge. Computes the
    stale refs (in DB, not in fresh_refs) up front, then batches the DELETEs over just those stale
    refs so a later batch's rows are never wiped by an earlier batch's NOT-IN scope; deletes
    embeddings first so no orphan vectors remain."""
    fresh_set = set(fresh_refs)
    with conn.cursor() as cur:
        cur.execute("SELECT source_ref FROM evidence WHERE source = %s", (source,))
        db_refs = [r[0] for r in cur.fetchall()]
    stale = [ref for ref in db_refs if ref not in fresh_set]

    deleted = 0
    for start in range(0, len(stale), batch):
        chunk = stale[start:start + batch]
        for attempt in range(6):
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM evidence_embeddings WHERE evidence_id IN "
                        "(SELECT id FROM evidence WHERE source = %s AND source_ref = ANY(%s))",
                        (source, chunk),
                    )
                    cur.execute(
                        "DELETE FROM evidence WHERE source = %s AND source_ref = ANY(%s)",
                        (source, chunk),
                    )
                    deleted += cur.rowcount
                conn.commit()
                break
            except psycopg2.errors.SerializationFailure:
                conn.rollback()
                if attempt == 5:
                    raise
                time.sleep(0.5 * (2 ** attempt))
    return deleted

def selftest():
    fails = []
    def check(label, cond):
        if not cond: fails.append(label)
        print(("PASS" if cond else "FAIL") + ": " + label)

    ddl = all_ddl()
    check("evidence table in DDL", "CREATE TABLE IF NOT EXISTS evidence" in ddl)
    check("evidence_embeddings table in DDL", "CREATE TABLE IF NOT EXISTS evidence_embeddings" in ddl)
    check("evidence has source col", "source " in ddl and "source_ref" in ddl)
    check("evidence has professor_slug + course_code", "professor_slug" in ddl and "course_code" in ddl)
    check("evidence has body_tsv TSVECTOR", "body_tsv" in ddl and "TSVECTOR" in ddl)
    check("evidence has body_sha", "body_sha" in ddl)
    check("embeddings track body_sha (in embeddings table, not just evidence)", "body_sha" in ddl.split("evidence_embeddings")[1])
    check("evidence has rmp_meta JSONB", "rmp_meta" in ddl and "JSONB" in ddl)
    check("evidence has subreddit + created_utc", "subreddit" in ddl and "created_utc" in ddl)
    check("evidence dedupe UNIQUE is plain 4-col (CRDB rejects expr conflict target)",
          "UNIQUE (source, source_ref, professor_slug, course_code)" in ddl and "COALESCE(course_code" not in ddl)
    check("course_code is NOT NULL empty-string sentinel", "course_code   TEXT NOT NULL DEFAULT ''" in ddl)
    check("GIN index on body_tsv", "USING GIN" in ddl and "body_tsv" in ddl)
    check("btree index on professor_slug", "ev_prof" in ddl)
    check("btree index on course_code", "ev_course" in ddl)
    check("embeddings VECTOR(384)", "VECTOR(384)" in ddl)
    check("embeddings model_version", "model_version" in ddl)
    check("NO global vector index (entity-scoped brute-force by design; backfill contention)",
          "ev_embed_idx" not in ddl and "VECTOR INDEX" not in ddl)

    # ── norm_code ──
    check("norm_code uppercases + strips space", norm_code("cs 3500") == "CS3500")
    check("norm_code on clean code", norm_code("EECE2140") == "EECE2140")

    # ── is_meaningful (TRACE filter) ──
    check("meaningful keeps a real comment", is_meaningful("Great professor, very clear lectures") is True)
    check("meaningful drops n/a", is_meaningful("N/A") is False)
    check("meaningful drops blank", is_meaningful("   ") is False)
    check("meaningful drops <15 chars", is_meaningful("good prof") is False)

    # ── dedup_key ──
    check("dedup_key collapses whitespace + case + caps at 80",
          dedup_key("Great   Prof") == dedup_key("great prof"))

    # ── Reddit rows: one per mention, sentiment + score carried ──
    def reddit_q(sql, params=None):
        if "reddit_mentions" in sql:
            return [{"source_id": "c1", "professor_slug": "guha-prof", "body": "hard but fair, great office hours",
                     "subreddit": "NEU", "score": 12, "permalink": "/r/x",
                     "sentiment": "positive", "flagged": False}]
        return []
    rr = build_reddit_rows(reddit_q)
    check("reddit row source tag", rr[0]["source"] == "reddit")
    check("reddit row keyed to prof", rr[0]["professor_slug"] == "guha-prof")
    check("reddit row carries sentiment", rr[0]["sentiment"] == "positive")
    check("reddit row carries score", rr[0]["reddit_score"] == 12)
    check("reddit row course_code is '' sentinel (never None)", rr[0]["course_code"] == "")
    check("reddit row has body_sha", len(rr[0]["body_sha"]) == 32)
    check("reddit row carries subreddit", rr[0]["subreddit"] == "NEU")

    # ── RMP rows: course_code only on exact match to the prof's TRACE set ──
    def rmp_q(sql, params=None):
        if "rmp_reviews" in sql:
            return [
                {"id": "r1", "name_key": "olin guha", "course": "CS3500", "comment": "Tough grader but I learned a ton in this class",
                 "quality": 5, "difficulty": 4, "tags": "GIVES GOOD FEEDBACK", "grade": "A"},
                {"id": "r2", "name_key": "olin guha", "course": "Algorithms", "comment": "Loved the material and the pacing of the course",
                 "quality": 4, "difficulty": 3, "tags": "", "grade": "B"},
            ]
        if "professors_catalog" in sql:
            return [{"name_key": "olin guha", "slug": "guha-prof"}]
        if "trace_courses" in sql:  # courses this prof actually taught
            return [{"name_key": "olin guha", "course_code": "CS3500"}]
        return []
    mr = build_rmp_rows(rmp_q)
    check("rmp row tagged rmp", all(r["source"] == "rmp" for r in mr))
    check("rmp keyed to prof slug", all(r["professor_slug"] == "guha-prof" for r in mr))
    check("rmp validated code keeps course_code", mr[0]["course_code"] == "CS3500")
    check("rmp stale/name course -> course_code '' (kept, prof-only)", mr[1]["course_code"] == "")
    check("rmp carries meta", mr[0]["rmp_meta"]["quality"] == 5 and mr[0]["rmp_meta"]["grade"] == "A")

    # ── TRACE rows: keyed to BOTH prof + course via join; filtered + deduped ──
    def trace_q(sql, params=None):
        if "trace_comments" in sql:
            return [
                {"id": "t1", "comment": "The instructor explained recursion exceptionally well",
                 "name_key": "olin guha", "course_code": "CS3500", "professor_slug": "guha-prof"},
                {"id": "t2", "comment": "N/A", "name_key": "olin guha", "course_code": "CS3500", "professor_slug": "guha-prof"},
                {"id": "t3", "comment": "The instructor explained recursion exceptionally well",
                 "name_key": "olin guha", "course_code": "CS3500", "professor_slug": "guha-prof"},  # dup of t1
            ]
        return []
    tr = build_trace_rows(trace_q)
    check("trace row tagged trace", all(r["source"] == "trace" for r in tr))
    check("trace keyed to prof AND course", tr[0]["professor_slug"] == "guha-prof" and tr[0]["course_code"] == "CS3500")
    check("trace drops n/a + dedupes (3 in -> 1 out)", len(tr) == 1)
    check("trace sentiment is None", tr[0]["sentiment"] is None)

    # ── Issue 23: dedupe representative is deterministic regardless of SELECT order ──
    def trace_q_order_a(sql, params=None):
        if "trace_comments" in sql:
            return [
                {"id": "t1", "comment": "The instructor explained recursion exceptionally well",
                 "name_key": "olin guha", "course_code": "CS3500", "professor_slug": "guha-prof"},
                {"id": "t3", "comment": "The instructor explained recursion exceptionally well",
                 "name_key": "olin guha", "course_code": "CS3500", "professor_slug": "guha-prof"},
            ]
        return []
    def trace_q_order_b(sql, params=None):
        if "trace_comments" in sql:
            return sorted(trace_q_order_a(sql, params), key=lambda r: r["id"])
        return []
    trace_sql_holder = []
    def trace_q_capture_sql(sql, params=None):
        trace_sql_holder.append(sql)
        return []
    build_trace_rows(trace_q_capture_sql)
    check("trace SELECT orders by tc.id (deterministic dedupe representative)",
          "ORDER BY tc.id" in trace_sql_holder[0])
    # Because the SQL orders by tc.id, the rows dedupe sees are always in id order regardless of
    # any unordered re-run of the same query — simulate that guarantee by feeding both an
    # already-sorted list (as the real ORDER BY would produce) from two independently-run mocks.
    tr_a = build_trace_rows(trace_q_order_a)
    tr_b = build_trace_rows(trace_q_order_b)
    check("dedupe keeps first-seen (lowest id) row consistently once query is ordered by tc.id",
          tr_a[0]["source_ref"] == tr_b[0]["source_ref"] == "t1")

    # ── Issue 24: upsert refreshes mutable metadata columns, not just body/flagged ──
    upsert_sql_holder = []
    class _FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None): upsert_sql_holder.append(sql); self.rowcount = 0
    class _FakeConn:
        def cursor(self, *a, **k): return _FakeCursor()
        def commit(self): pass
        def rollback(self): pass
    def _fake_execute_values(cur, sql, values, template=None):
        upsert_sql_holder.append(sql)
    import psycopg2.extras as _pge
    _saved = _pge.execute_values
    _pge.execute_values = _fake_execute_values
    try:
        upsert_evidence(_FakeConn(), [_row("reddit", "r1", "guha-prof", None, "great professor and clear lectures")])
    finally:
        _pge.execute_values = _saved
    upsert_sql = re.sub(r"\s+", " ", upsert_sql_holder[-1])
    for col in ("sentiment", "reddit_score", "permalink", "created_utc", "rmp_meta", "subreddit"):
        check(f"upsert SET-list refreshes {col}", f"{col} = excluded.{col}" in upsert_sql)

    # ── Issue 24: prune_evidence deletes embeddings then evidence, scoped to source + stale refs ──
    prune_sql_holder = []
    class _FakePruneCursor:
        def __init__(self): self.rowcount = 0
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            if "SELECT source_ref FROM evidence" in sql:
                return
            prune_sql_holder.append((sql, params))
            self.rowcount = 2
        def fetchall(self):
            return [("keep1",), ("keep2",), ("stale1",)]
    class _FakePruneConn:
        def cursor(self, *a, **k): return _FakePruneCursor()
        def commit(self): pass
        def rollback(self): pass
    deleted = prune_evidence(_FakePruneConn(), "reddit", ["keep1", "keep2"])
    check("prune deletes embeddings before evidence", "evidence_embeddings" in prune_sql_holder[0][0])
    check("prune scopes DELETE to source + stale refs only",
          prune_sql_holder[1][1][0] == "reddit" and prune_sql_holder[1][1][1] == ["stale1"]
          and "source_ref = ANY" in prune_sql_holder[1][0] and "NOT" not in prune_sql_holder[1][0])
    check("prune returns deleted count from cursor.rowcount", deleted == 2)

    # ── Critical: prune must batch over STALE refs (DB minus fresh), not fresh refs — batching
    # over fresh refs means the first chunk's NOT-IN DELETE wipes every fresh row whose ref is in
    # a LATER chunk, destroying nearly the whole corpus once there's more than one batch. ──
    prune_multi_sql_holder = []
    class _FakeMultiBatchCursor:
        def __init__(self): self.rowcount = 0
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            if "SELECT source_ref FROM evidence" in sql:
                self._select = True
            else:
                self._select = False
                prune_multi_sql_holder.append((sql, params))
            self.rowcount = 1
        def fetchall(self):
            # DB holds refs spanning MORE than one batch (batch=2): a,b,c,d are fresh; stale1/
            # stale2/stale3 are stale and span 2 batches of stale refs (2 + 1).
            return [("a",), ("b",), ("c",), ("d",), ("stale1",), ("stale2",), ("stale3",)]
    class _FakeMultiBatchConn:
        def cursor(self, *a, **k): return _FakeMultiBatchCursor()
        def commit(self): pass
        def rollback(self): pass
    fresh_refs_multi = ["a", "b", "c", "d"]
    deleted_multi = prune_evidence(_FakeMultiBatchConn(), "trace", fresh_refs_multi, batch=2)
    all_delete_params = [p for _, p in prune_multi_sql_holder]
    all_chunk_refs = [ref for _source, chunk in all_delete_params for ref in chunk]
    check("prune (multi-batch) never puts a fresh ref in any DELETE param",
          not any(ref in fresh_refs_multi for ref in all_chunk_refs))
    check("prune (multi-batch) DELETE params only ever contain stale refs",
          set(all_chunk_refs) == {"stale1", "stale2", "stale3"})
    check("prune (multi-batch) issues 2 DELETEs per stale chunk, 2 stale chunks (4 total)",
          len(prune_multi_sql_holder) == 4)
    check("prune (multi-batch) deletes embeddings before evidence within each stale chunk",
          "evidence_embeddings" in prune_multi_sql_holder[0][0]
          and "evidence_embeddings" not in prune_multi_sql_holder[1][0]
          and "evidence_embeddings" in prune_multi_sql_holder[2][0]
          and "evidence_embeddings" not in prune_multi_sql_holder[3][0])
    check("prune (multi-batch) returns deleted count summed across stale chunks", deleted_multi == 2)

    print("ALL PASS" if not fails else f"{len(fails)} FAIL(s): " + ", ".join(fails))
    return 1 if fails else 0

def main():
    parser = argparse.ArgumentParser(description="Build evidence corpus in CockroachDB")
    parser.add_argument("--selftest", action="store_true", help="Run offline DDL checks")
    parser.add_argument("--build-evidence", action="store_true", help="Populate evidence from all sources")
    parser.add_argument("--prune", action="store_true",
                         help="After building, delete evidence rows (+ their embeddings) whose "
                              "source_ref is no longer produced by the build (requires --build-evidence)")
    args = parser.parse_args()

    if args.selftest:
        sys.exit(selftest())

    if args.build_evidence:
        import psycopg2.extras
        conn = connect()
        ensure_schema(conn)

        def query_fn(sql, params=None):
            with conn.cursor(name=f"ev_cur_{next(_counter)}", cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params or ())
                return cur.fetchall()

        reddit_rows = build_reddit_rows(query_fn)
        rmp_rows = build_rmp_rows(query_fn)
        trace_rows = build_trace_rows(query_fn)
        all_rows = reddit_rows + rmp_rows + trace_rows
        n = upsert_evidence(conn, all_rows)
        print(f"Upserted {n} evidence rows")

        if args.prune:
            for source, rows in (("reddit", reddit_rows), ("rmp", rmp_rows), ("trace", trace_rows)):
                fresh_refs = [r["source_ref"] for r in rows]
                deleted = prune_evidence(conn, source, fresh_refs)
                print(f"Pruned {deleted} stale {source} evidence rows")

        conn.close()
        sys.exit(0)

    print("Use --selftest for offline checks or --build-evidence to populate")
    sys.exit(0)

if __name__ == "__main__":
    main()
