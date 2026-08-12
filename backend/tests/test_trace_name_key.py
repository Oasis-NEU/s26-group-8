"""Tests for the TRACE name a fuzzy-matched professor's scores are filed under.

RMP and TRACE spell the same professor differently often enough that precompute
falls back to a surname match when the normalized names do not agree. The match
itself worked; what it did not do was record *which* TRACE name it picked. Since
every read path joins TRACE data on `name_key`, those professors displayed a
TRACE rating with no courses, no comments and no rating distribution behind it —
50 of them in production on the 2026-08-11 corpus.

professor_full.trace_key() already reads a `trace_name_key` column to fix that.
Nothing wrote it, so it always fell back to name_key and the fix was inert. These
tests pin the writer.

NULL means "no fuzzy match happened", which covers both the exact-match case and
a catalog built before the column existed. trace_key() falls back to name_key for
those, so only the fuzzy matches need storing.

Both halves are pinned here — the precompute writer and the professor_full
reader — because the bug was that they disagreed, and a test on either one alone
passes while the pair stays broken.

No database: attach_fuzzy_trace is pure, and the read path takes injectable
query functions.
"""

import pathlib
import re

import pandas as pd

from precompute import attach_fuzzy_trace, catalog_comment_count
from professor_full import build_full, trace_key

PRECOMPUTE_PY = pathlib.Path(__file__).resolve().parents[1] / "precompute.py"


def _profs(*name_keys):
    """An rmp_profs frame at the point the fuzzy match runs: the exact-match
    lookups have already been applied, so an unmatched professor is one whose
    trace_overall is still NaN."""
    return pd.DataFrame({
        "_name_key": list(name_keys),
        "trace_overall": [float("nan")] * len(name_keys),
        "trace_reviews": [0] * len(name_keys),
        "trace_dept": [None] * len(name_keys),
        "avg_hours": [float("nan")] * len(name_keys),
    })


TRACE = {"daniel koloski": 4.5}
REVIEWS = {"daniel koloski": 120}
DEPTS = {"daniel koloski": "Multidisciplinary Graduate Engineering"}
HOURS = {"daniel koloski": 8.0}


# ── what the fuzzy match records ────────────────────────────────────────────

def test_fuzzy_match_records_the_trace_name_it_matched():
    """The whole point: "dan koloski" resolves to TRACE's "daniel koloski", and
    the read path needs to know that to find his courses."""
    profs = _profs("dan koloski")
    attach_fuzzy_trace(profs, TRACE, REVIEWS, DEPTS, HOURS)
    assert profs.at[0, "_trace_name_key"] == "daniel koloski"


def test_fuzzy_match_still_attaches_the_trace_data():
    """Recording the name must not disturb what the match already did."""
    profs = _profs("dan koloski")
    attach_fuzzy_trace(profs, TRACE, REVIEWS, DEPTS, HOURS)
    assert profs.at[0, "trace_overall"] == 4.5
    assert profs.at[0, "trace_reviews"] == 120
    assert profs.at[0, "trace_dept"] == "Multidisciplinary Graduate Engineering"
    assert profs.at[0, "avg_hours"] == 8.0


def test_exactly_matched_professor_gets_no_trace_name_key():
    """An exact match needs no stored name — trace_key() falls back to name_key,
    and storing a duplicate of it would be a second copy to keep in sync."""
    profs = _profs("daniel koloski")
    profs.at[0, "trace_overall"] = 4.5   # already matched by name, so not unmatched
    attach_fuzzy_trace(profs, TRACE, REVIEWS, DEPTS, HOURS)
    assert profs.at[0, "_trace_name_key"] is None


def test_nickname_that_is_not_a_prefix_does_not_match():
    """Scope, and deliberately narrow: the rule needs one first name to be a
    prefix of the other, so "meg" never reaches "margaret". Those professors are
    handled by prof_aliases.ALIAS_MAP by hand instead — a surname plus a shared
    initial would match unrelated people, and a wrong match files a professor's
    rating under someone else's courses."""
    profs = _profs("meg heckman")
    attach_fuzzy_trace(profs, {"margaret heckman": 4.5}, {}, {}, {})
    assert profs.at[0, "_trace_name_key"] is None
    assert pd.isna(profs.at[0, "trace_overall"])


def test_unmatched_professor_gets_no_trace_name_key():
    """No TRACE data found at all — nothing to point at."""
    profs = _profs("nobody withthisname")
    attach_fuzzy_trace(profs, TRACE, REVIEWS, DEPTS, HOURS)
    assert profs.at[0, "_trace_name_key"] is None


def test_column_exists_even_when_nothing_matches():
    """The catalog build reads this column for every row, so it has to be there
    on a corpus where no professor needed a fuzzy match at all."""
    profs = _profs()
    attach_fuzzy_trace(profs, TRACE, REVIEWS, DEPTS, HOURS)
    assert "_trace_name_key" in profs.columns


def test_returns_how_many_professors_were_matched():
    """main() prints this, the same way it reports the recount and the blend."""
    profs = _profs("dan koloski", "nobody withthisname")
    assert attach_fuzzy_trace(profs, TRACE, REVIEWS, DEPTS, HOURS) == 1


# ── guards against matching a different person ──────────────────────────────
# A first name being a prefix of another is not evidence of the same person:
# "michael" is a prefix of "michaela" and "yan" of "yaning", the same way "dan"
# is of "daniel".
#
# Department was tried as a guard and removed. Cross-college teaching is common
# enough that a college mismatch rejected three correct matches -- Lungeanu
# (Business/Communication Studies), Koloski (Business/Grad Engineering),
# Laverdiere (Counseling/Applied Psychology) -- for the one collision it caught.
# Measured on the 2026-08-11 corpus: 50 matches down to 46.

def test_professor_with_their_own_trace_courses_is_not_fuzzy_matched():
    """The Michaela Lewis case, and the guard that needs no hand-maintained list.

    She has nine TRACE courses and no survey responses, so trace_overall is NaN
    for want of scores rather than for want of a name. TRACE knows exactly who
    she is; there is no other spelling to go looking for."""
    profs = _profs("michaela lewis")
    attach_fuzzy_trace(profs, {"michael lewis": 3.89}, {"michael lewis": 9},
                       {"michael lewis": "Law"}, {},
                       trace_name_keys={"michaela lewis", "michael lewis"})
    assert profs.at[0, "_trace_name_key"] is None
    assert pd.isna(profs.at[0, "trace_overall"]), \
        "refusing the name must also leave the rating unattached"


def test_professor_absent_from_trace_is_still_matched():
    """The guard must not cost the 49 matches that were right all along: an RMP
    professor with no TRACE courses of their own is exactly the case the fuzzy
    match exists for."""
    profs = _profs("dan koloski")
    attach_fuzzy_trace(profs, TRACE, REVIEWS, DEPTS, HOURS,
                       trace_name_keys={"daniel koloski"})
    assert profs.at[0, "_trace_name_key"] == "daniel koloski"


def test_denied_pair_is_not_matched():
    """Yan Li has no TRACE courses under her own name, so the check above cannot
    see her and nothing lexical separates her from a nickname. Hand-listed."""
    profs = _profs("yan li")
    attach_fuzzy_trace(profs, {"yaning li": 3.93}, {"yaning li": 197},
                       {"yaning li": "Mech  Industrial Engineering"}, {},
                       trace_name_keys={"yaning li"})
    assert profs.at[0, "_trace_name_key"] is None


def test_deny_list_only_blocks_the_pair_it_names():
    """Denying ("yan li", "yaning li") must not deny Yan Li a different match,
    nor block someone else from matching "yaning li"."""
    profs = _profs("yan li")
    attach_fuzzy_trace(profs, {"yan lin li": 4.0}, {"yan lin li": 20},
                       {"yan lin li": "History"}, {}, trace_name_keys={"yan lin li"})
    assert profs.at[0, "_trace_name_key"] == "yan lin li"


def test_a_denied_candidate_does_not_block_a_later_good_one():
    """The loop takes the first surname candidate that passes, so a denied one
    appearing first must be skipped rather than end the search."""
    profs = _profs("yan li")
    attach_fuzzy_trace(
        profs,
        {"yaning li": 3.93, "yan lin li": 4.0},
        {"yaning li": 197, "yan lin li": 20},
        {"yaning li": "Engineering", "yan lin li": "History"},
        {}, trace_name_keys={"yaning li", "yan lin li"})
    assert profs.at[0, "_trace_name_key"] == "yan lin li"


# ── the plumbing that carries it to the database ────────────────────────────
# The column reaching the DB depends on three lists agreeing: the DDL, the INSERT
# and the row tuple. Nothing else in the suite reads them, which is how the
# reader shipped without a writer in the first place.

def _ddl_columns():
    src = PRECOMPUTE_PY.read_text()
    ddl = re.search(r"CREATE TABLE professors_catalog_new \((.*?)\n\s*\)\n", src, re.S).group(1)
    return [line.split()[0] for line in
            (l.strip() for l in ddl.strip().splitlines())
            if line and not line.startswith("--")]


def _insert_columns():
    src = PRECOMPUTE_PY.read_text()
    cols = re.search(r"INSERT INTO professors_catalog_new\s*\n\s*\((.*?)\)\s*\n\s*VALUES",
                     src, re.S).group(1)
    return [c.strip() for c in cols.replace("\n", " ").split(",")]


def test_catalog_ddl_declares_trace_name_key():
    assert "trace_name_key" in _ddl_columns()


def test_catalog_insert_lists_the_columns_the_ddl_declares():
    """In the same order: the INSERT names its columns, so a mismatch would land
    values in the wrong ones rather than failing loudly."""
    assert _insert_columns() == _ddl_columns()


def _appended_row_widths():
    """How many values each `catalog_rows.append((...))` passes. Parsed rather
    than counted by eye: the tuples are 20 items of mostly-conditional
    expressions, spread over a dozen lines each."""
    import ast
    tree = ast.parse(PRECOMPUTE_PY.read_text())
    return [len(node.args[0].elts) for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "catalog_rows"
            and isinstance(node.args[0], ast.Tuple)]


def test_every_catalog_row_is_as_wide_as_the_insert():
    """Both the RMP-side and TRACE-only tuples. Adding a column to the DDL and
    the INSERT while missing one of the two appends fails only at execute_values
    time, against the real database, halfway through a production rebuild."""
    widths = _appended_row_widths()
    assert len(widths) == 2, f"expected two append sites, found {len(widths)}"
    assert widths == [len(_insert_columns())] * 2


# ── the stored comment count ────────────────────────────────────────────────
# total_comments is the same bug in a second column. Its two halves are filed
# under different names for a fuzzy-matched professor — RMP comments under the
# RMP spelling, TRACE comments under TRACE's — so one combined lookup drops one
# half whichever key it uses. server.py's leaderboard sums the halves separately
# already; the stored column has to agree with it or the catalog list and the
# board disagree about the same professor.

RMP_COUNTS = {"dan koloski": 4, "daniel koloski": 0}
TRACE_COUNTS = {"daniel koloski": 881, "dan koloski": 0}


def test_fuzzy_matched_professor_counts_both_halves():
    """The bug: 4 RMP comments under "dan koloski" plus 881 TRACE comments under
    "daniel koloski" is 885, not the 4 a name_key lookup finds."""
    assert catalog_comment_count(
        "dan koloski", "daniel koloski", RMP_COUNTS, TRACE_COUNTS) == 885


def test_keying_the_whole_count_on_either_name_alone_is_wrong():
    """Both single-key answers, pinned as the thing not to go back to: name_key
    finds the RMP comments and no TRACE ones, the TRACE name the reverse."""
    both = catalog_comment_count("dan koloski", "daniel koloski",
                                 RMP_COUNTS, TRACE_COUNTS)
    assert both != RMP_COUNTS["dan koloski"] + TRACE_COUNTS["dan koloski"]
    assert both != RMP_COUNTS["daniel koloski"] + TRACE_COUNTS["daniel koloski"]


def test_exact_match_uses_name_key_for_both_halves():
    """No trace_name_key means the two spellings are the same one."""
    assert catalog_comment_count(
        "virgil pavlu", None, {"virgil pavlu": 12}, {"virgil pavlu": 30}) == 42


def test_trace_only_professor_has_no_rmp_half():
    """Nothing in rmp_reviews under that name; the TRACE half stands alone."""
    assert catalog_comment_count(
        "jessica marengo", None, {}, {"jessica marengo": 546}) == 546


def test_professor_with_no_comments_at_all_counts_zero():
    """Neither lookup has the key — the column is NOT NULL-ish downstream, so
    this must be 0 rather than None."""
    assert catalog_comment_count("nobody withthisname", None, {}, {}) == 0


# ── the read path ───────────────────────────────────────────────────────────
# A stored trace_name_key is only worth writing if the queries use it. Every
# TRACE-side lookup has to key on it and every RMP-side lookup must not.

RMP_KEY = "dan koloski"
TRACE_NAME = "daniel koloski"


class KeyRecordingQuery:
    """Injectable query/query_one that records which key each table was asked for.

    Returns one plausible row per table rather than empty results: build_full
    short-circuits parts of the payload when a table comes back empty, and a
    query that never runs cannot be asserted about.
    """

    def __init__(self, trace_name_key):
        self.keys = {}      # table -> the parameter it was queried with
        self._trace_name_key = trace_name_key

    def _table(self, sql):
        for table in ("professors_catalog", "rmp_reviews", "trace_comments",
                      "reddit_mentions", "trace_scores", "trace_courses"):
            if f"from {table}" in sql.lower():
                return table
        return None

    def _rows_for(self, table):
        if table == "professors_catalog":
            return [{"name": "Dan Koloski", "slug": "dan-koloski", "name_key": RMP_KEY,
                     "trace_name_key": self._trace_name_key,
                     "department": "Multidisciplinary Graduate Engineering",
                     "rmp_rating": 4.1, "trace_rating": 4.3, "avg_rating": 4.2,
                     "difficulty": 3.5, "would_take_again_pct": 88.0,
                     "total_reviews": 31, "professor_url": None, "image_url": None,
                     "avg_hours": 6.0}]
        if table == "trace_courses":
            return [{"course_id": 1, "term_id": 901, "term_title": "Fall 2023",
                     "department_name": "Engineering", "display_name": "EMGT6225: Systems",
                     "section": "1", "enrollment": 40, "instructor_id": 7}]
        if table == "trace_scores":
            return [{"course_id": 1, "term_id": 901,
                     "display_name": "EMGT6225: Systems", "question": "Overall rating",
                     "mean": 4.5, "count_1": 0, "count_2": 0, "count_3": 1,
                     "count_4": 2, "count_5": 7, "completed": 10}]
        if table == "rmp_reviews":
            return [{"course": "EMGT6225", "quality": 5, "difficulty": 3, "date": "2024",
                     "tags": "", "attendance": "", "grade": "A", "textbook": "",
                     "online_class": "", "comment": "Great teacher."}]
        return []

    def query(self, sql, params=None):
        table = self._table(sql)
        if table and params:
            self.keys[table] = params[0]
        return self._rows_for(table)

    def query_one(self, sql, params=None):
        rows = self.query(sql, params)
        return rows[0] if rows else None


def _run(trace_name_key):
    q = KeyRecordingQuery(trace_name_key)
    build_full("dan-koloski", q.query, q.query_one, lambda s: s)
    return q.keys


def test_trace_courses_are_fetched_under_the_trace_name():
    """The professor's course list, and the join keys every TRACE comment is
    found through. Under the RMP name this returns nothing."""
    assert _run(TRACE_NAME)["trace_courses"] == TRACE_NAME


def test_trace_scores_are_scanned_under_the_trace_name():
    """The rating distribution and the challenge/hours aggregates."""
    assert _run(TRACE_NAME)["trace_scores"] == TRACE_NAME


def test_rmp_reviews_are_still_fetched_under_the_rmp_name():
    """The other half of the invariant, and the reason trace_key() is a separate
    function rather than a rewrite of name_key: RMP reviews are stored under the
    RMP spelling, so redirecting this one would empty the review list instead."""
    assert _run(TRACE_NAME)["rmp_reviews"] == RMP_KEY


def test_exactly_matched_professor_reads_everything_under_its_own_key():
    """trace_name_key NULL — the common case, and every catalog row in production
    until precompute next runs."""
    keys = _run(None)
    assert keys["trace_courses"] == RMP_KEY
    assert keys["trace_scores"] == RMP_KEY
    assert keys["rmp_reviews"] == RMP_KEY


def test_trace_key_falls_back_when_the_column_is_absent_entirely():
    """A catalog built before the column existed: absent, not NULL."""
    assert trace_key({"name_key": RMP_KEY}) == RMP_KEY


# ── the routes that stayed in server.py ─────────────────────────────────────
# professor_full.py only extracted the unauthenticated /full path. The
# authenticated profile and the reviews route kept their own TRACE lookups, so
# they need the same key or the same professor is empty for logged-in readers.

CATALOG_ROW = {
    "name": "Dan Koloski", "slug": "dan-koloski", "name_key": RMP_KEY,
    "trace_name_key": TRACE_NAME,
    "department": "Multidisciplinary Graduate Engineering",
    "college": "Engineering", "rmp_rating": 4.1, "trace_rating": 4.3,
    "avg_rating": 4.2, "difficulty": 3.5, "would_take_again_pct": 88.0,
    "num_ratings": 12, "trace_reviews": 19, "total_reviews": 31,
    "professor_url": None, "image_url": None, "focus_x": 50.0, "focus_y": 30.0,
    "avg_hours": 6.0, "total_comments": 4,
}

TRACE_COURSE_ROW = {
    "course_id": 1, "instructor_id": 7, "term_id": 901, "term_title": "Fall 2023",
    "department_name": "Engineering", "display_name": "EMGT6225: Systems",
    "section": "1", "enrollment": 40,
}

TRACE_SCORE_ROW = {
    "course_id": 1, "term_id": 901, "instructor_id": 7,
    "display_name": "EMGT6225: Systems", "question": "Overall Rating",
    "mean": 4.5, "count_1": 0, "count_2": 0, "count_3": 1, "count_4": 2,
    "count_5": 7, "completed": 10,
}


def _spy_server(monkeypatch, catalog_row):
    """Point server's query helpers at recorded fakes. Returns the recorder:
    table -> list of the keys it was queried with."""
    import server

    seen = {}

    def _table(sql):
        for t in ("professors_catalog", "rmp_reviews", "trace_comments",
                  "reddit_mentions", "trace_scores", "trace_courses"):
            if f"from {t}" in sql.lower():
                return t
        return None

    def record(sql, params=()):
        table = _table(sql)
        if table and params:
            seen.setdefault(table, []).append(params[0])
        return table

    def fake_query(sql, params=()):
        table = record(sql, params)
        if table == "trace_courses":
            return [dict(TRACE_COURSE_ROW)]
        if table == "trace_scores":
            return [dict(TRACE_SCORE_ROW)]
        return []

    def fake_query_one(sql, params=()):
        table = record(sql, params)
        return dict(catalog_row) if table == "professors_catalog" else None

    monkeypatch.setattr(server, "query", fake_query, raising=False)
    monkeypatch.setattr(server, "query_one", fake_query_one, raising=False)
    monkeypatch.setattr(server, "cache_get", lambda key: None, raising=False)
    monkeypatch.setattr(server, "cache_set", lambda key, data: None, raising=False)
    return server, seen


def test_profile_route_reads_trace_under_the_trace_name(monkeypatch):
    """The unauthenticated branch: course list, challenge, overall and hours."""
    server, seen = _spy_server(monkeypatch, CATALOG_ROW)
    resp = server.app.test_client().get("/api/professors/dan-koloski")
    assert resp.status_code == 200
    assert set(seen["trace_courses"]) == {TRACE_NAME}
    assert set(seen["trace_scores"]) == {TRACE_NAME}


def test_reviews_route_reads_trace_under_the_trace_name(monkeypatch):
    server, seen = _spy_server(monkeypatch, CATALOG_ROW)
    resp = server.app.test_client().get("/api/professors/dan-koloski/reviews")
    assert resp.status_code == 200
    assert set(seen["trace_courses"]) == {TRACE_NAME}


def test_reviews_route_still_reads_rmp_under_the_rmp_name(monkeypatch):
    """The route with both sides in it, so the one most able to get this wrong."""
    server, seen = _spy_server(monkeypatch, CATALOG_ROW)
    server.app.test_client().get("/api/professors/dan-koloski/reviews")
    assert set(seen["rmp_reviews"]) == {RMP_KEY}


def test_reviews_route_selects_the_column_it_needs(monkeypatch):
    """The route used to SELECT an explicit column list from the catalog, which
    would leave trace_name_key absent and the fix silently inert."""
    server, seen = _spy_server(monkeypatch, CATALOG_ROW)
    server.app.test_client().get("/api/professors/dan-koloski/reviews")
    assert set(seen["trace_courses"]) == {TRACE_NAME}, "catalog row lost trace_name_key"


def test_routes_fall_back_to_name_key_when_not_fuzzy_matched(monkeypatch):
    """Every professor in production until precompute next runs."""
    server, seen = _spy_server(monkeypatch, {**CATALOG_ROW, "trace_name_key": None})
    server.app.test_client().get("/api/professors/dan-koloski")
    assert set(seen["trace_courses"]) == {RMP_KEY}
    assert set(seen["trace_scores"]) == {RMP_KEY}


# ── the RAG corpus builder joins on the same key ────────────────────────────
#
# server.py, professor_full.trace_key and precompute.catalog_comment_count were
# all taught to read TRACE under trace_name_key. scraper/load_evidence_to_crdb.py
# was not, and it is the one that decides what the chat can retrieve — so for the
# ~50 fuzzy-matched professors the profile page showed TRACE comments the chat
# could not see, and the two halves of the site disagreed about the same person.
#
# Driven through sqlite rather than asserted as SQL text: the join is the
# behaviour under test, and a substring check would pass on a query that no
# longer runs.

import sqlite3
import sys as _sys

_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scraper"))


def _evidence_db(trace_name_key, course_key):
    """A catalog row, one TRACE course under `course_key`, one comment on it."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE professors_catalog (slug TEXT, name_key TEXT, trace_name_key TEXT);
        CREATE TABLE trace_courses (course_id INT, instructor_id INT, term_id INT,
                                    name_key TEXT, course_code TEXT);
        CREATE TABLE trace_comments (id INT, comment TEXT, tc_course_id INT,
                                     tc_instructor_id INT, tc_term_id INT);
    """)
    db.execute("INSERT INTO professors_catalog VALUES (?,?,?)",
               ("dan-koloski", RMP_KEY, trace_name_key))
    db.execute("INSERT INTO trace_courses VALUES (?,?,?,?,?)",
               (1, 7, 901, course_key, "EMGT6225"))
    db.execute("INSERT INTO trace_comments VALUES (?,?,?,?,?)",
               (11, "Professor Koloski explained the material clearly and gave "
                    "genuinely helpful feedback on every assignment.", 1, 7, 901))

    def query_fn(sql, params=()):
        return [dict(r) for r in db.execute(sql, params).fetchall()]

    return query_fn


def test_trace_evidence_is_built_for_a_fuzzy_matched_professor():
    """The catalog holds the RMP spelling; the course rows hold TRACE's."""
    from load_evidence_to_crdb import build_trace_rows
    rows = build_trace_rows(_evidence_db(TRACE_NAME, TRACE_NAME))
    assert len(rows) == 1, "the fuzzy-matched professor's TRACE comments were dropped"
    assert rows[0]["professor_slug"] == "dan-koloski"


def test_trace_evidence_still_builds_for_an_exactly_matched_professor():
    """trace_name_key is NULL unless a fuzzy match happened — fall back, not skip."""
    from load_evidence_to_crdb import build_trace_rows
    rows = build_trace_rows(_evidence_db(None, RMP_KEY))
    assert len(rows) == 1
    assert rows[0]["professor_slug"] == "dan-koloski"


# ── the RMP half of the same join ───────────────────────────────────────────
#
# build_rmp_rows validates a review's course code against the courses TRACE says
# the professor taught, so a review claiming a course they never ran loses its
# attribution. `taught` is keyed on trace_courses.name_key (the TRACE spelling)
# but looked up with rmp_reviews.name_key (the RMP spelling), so for a
# fuzzy-matched professor the set is always empty and *every* one of their RMP
# reviews is stripped of its course — the validation silently becomes a reject-all.


def _rmp_db(trace_name_key, course_key):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE professors_catalog (name_key TEXT, slug TEXT, trace_name_key TEXT);
        CREATE TABLE trace_courses (name_key TEXT, course_code TEXT);
        CREATE TABLE rmp_reviews (id INT, name_key TEXT, course TEXT, comment TEXT,
                                  quality INT, difficulty INT, tags TEXT, grade TEXT);
    """)
    db.execute("INSERT INTO professors_catalog VALUES (?,?,?)",
               (RMP_KEY, "dan-koloski", trace_name_key))
    db.execute("INSERT INTO trace_courses VALUES (?,?)", (course_key, "ANLY6500"))

    def query_fn(sql, params=()):
        cur = db.execute(sql, params)
        return [dict(r) for r in cur.fetchall()] if cur.description else []

    return query_fn, db


def _add_review(db, course):
    db.execute("INSERT INTO rmp_reviews VALUES (1,?,?,?,4,3,'','A')",
               (RMP_KEY, course,
                "The lectures were genuinely well organised and the feedback was useful."))


def test_rmp_course_code_survives_the_rmp_trace_name_difference():
    from load_evidence_to_crdb import build_rmp_rows
    query_fn, db = _rmp_db(TRACE_NAME, TRACE_NAME)
    _add_review(db, "ANLY6500")
    rows = build_rmp_rows(query_fn)
    assert len(rows) == 1
    assert rows[0]["course_code"] == "ANLY6500", \
        "course attribution must survive the RMP/TRACE name difference"


def test_rmp_course_code_is_still_rejected_when_not_taught():
    """The validation has to stay a validation, not become a pass-through."""
    from load_evidence_to_crdb import build_rmp_rows
    query_fn, db = _rmp_db(TRACE_NAME, TRACE_NAME)
    _add_review(db, "PHIL1000")
    rows = build_rmp_rows(query_fn)
    assert rows[0]["course_code"] == "", "a course TRACE has no record of was attributed"


def test_rmp_course_code_still_validates_for_an_exact_match():
    from load_evidence_to_crdb import build_rmp_rows
    query_fn, db = _rmp_db(None, RMP_KEY)
    _add_review(db, "ANLY6500")
    rows = build_rmp_rows(query_fn)
    assert rows[0]["course_code"] == "ANLY6500"


# ── a fuzzy match must not also leave a TRACE-only row ──────────────────────
#
# attach_fuzzy_trace copies the TRACE scores onto the RMP row and records which
# TRACE name it took them from. The TRACE-only pass then has to know that name is
# spoken for. It only ever checked the RMP name_keys, so every fuzzy match left a
# second catalog row behind — "dan koloski" (blended) beside "daniel koloski"
# (TRACE-only), different slugs so nothing collided, both carrying the same TRACE
# reviews and both eligible for a GOATED slot.


def test_a_fuzzy_matched_trace_name_is_absorbed():
    from precompute import absorbed_trace_key
    assert absorbed_trace_key(TRACE_NAME, {RMP_KEY}, {TRACE_NAME})


def test_an_exactly_matched_name_is_absorbed_by_the_rmp_key():
    from precompute import absorbed_trace_key
    assert absorbed_trace_key("jane doe", {"jane doe"}, set())


def test_an_unmatched_trace_professor_still_gets_their_own_row():
    """The guard must not swallow genuine TRACE-only professors."""
    from precompute import absorbed_trace_key
    assert not absorbed_trace_key("someone else", {RMP_KEY}, {TRACE_NAME})


def test_the_catalog_build_feeds_the_guard_the_fuzzy_names():
    """The helper is only worth anything if main() collects _trace_name_key.

    Pinned at the source because the catalog build is inline in main() and has no
    seam to call — and a correct helper wired to an empty set is the bug intact.
    """
    src = PRECOMPUTE_PY.read_text()
    assert "absorbed_trace_key(" in src, "the TRACE-only loop does not use the guard"
    assert "_trace_name_key" in src.split("# TRACE-only professors")[0], \
        "fuzzy_trace_keys is never populated from _trace_name_key"


# ── the ghost-rating guard ──────────────────────────────────────────────────
#
# A catalog row that displays a TRACE rating whose key matches no trace_courses
# row is a "ghost": the number renders, and every TRACE panel behind it — course
# list, comments, distribution — comes back empty. That is exactly what a fuzzy
# match with no trace_name_key produced, and what a duplicate TRACE-only row
# produces. The detector and the bug were removed together, so nothing has been
# watching for it.
#
# In-memory and run *before* the old catalog is dropped, so it can still refuse
# the rebuild. A post-write SQL count can only report a live site already wrong.

from precompute import CATALOG_COLUMNS, unreachable_trace_rows  # noqa: E402


def _catalog_row(**fields):
    row = [None] * len(CATALOG_COLUMNS)
    for k, v in fields.items():
        row[CATALOG_COLUMNS.index(k)] = v
    return tuple(row)


def test_catalog_columns_match_the_insert():
    """The guard indexes positionally, so drift here reads the wrong column."""
    assert list(CATALOG_COLUMNS) == _insert_columns()


def test_a_rating_with_no_trace_rows_behind_it_is_reported():
    rows = [_catalog_row(name_key=RMP_KEY, trace_name_key=None, trace_rating=4.3)]
    assert unreachable_trace_rows(rows, {TRACE_NAME}) == [(RMP_KEY, RMP_KEY)]


def test_a_fuzzy_matched_row_resolves_through_trace_name_key():
    rows = [_catalog_row(name_key=RMP_KEY, trace_name_key=TRACE_NAME, trace_rating=4.3)]
    assert unreachable_trace_rows(rows, {TRACE_NAME}) == []


def test_an_exact_match_falls_back_to_name_key():
    rows = [_catalog_row(name_key="jane doe", trace_name_key=None, trace_rating=4.0)]
    assert unreachable_trace_rows(rows, {"jane doe"}) == []


def test_a_row_with_no_trace_rating_is_not_a_ghost():
    """An RMP-only professor legitimately has no TRACE rows."""
    rows = [_catalog_row(name_key=RMP_KEY, trace_name_key=None, trace_rating=None)]
    assert unreachable_trace_rows(rows, set()) == []


def test_the_rebuild_calls_the_ghost_guard():
    """A guard nothing calls is not a guard.

    Matched as a Call node, not a substring: the function's own `def` line
    contains its name, so a text search passes on a module that never runs it.
    """
    import ast
    tree = ast.parse(PRECOMPUTE_PY.read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "unreachable_trace_rows"]
    assert calls, "the rebuild never calls unreachable_trace_rows"


# ── the two aliases no automatic rule can recover ───────────────────────────

def test_the_two_peter_xus_stay_separate_people():
    """Same surname, same department, both teaching a 2301 supply-chain course.

    attach_fuzzy_trace needs a shared first-token prefix and neither pair has
    one ("peter" vs "peng", "peter" vs "xun"), so these cannot be rediscovered —
    they are the residue that has to be written down. Collapsing them would merge
    two people's ratings; the review dates alone rule it out, since Peng's start
    in 2023 and Xun has no sections before Spring 2025.
    """
    from prof_aliases import ALIAS_MAP
    assert ALIAS_MAP.get("peter xu") == "peng xu"
    assert ALIAS_MAP.get("peter (xun) xu") == "xun xu"
    assert ALIAS_MAP["peter xu"] != ALIAS_MAP["peter (xun) xu"]
