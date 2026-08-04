"""Evidence rows must follow a fuzzy-matched professor's TRACE name.

precompute stores a professor's catalog row under the RMP spelling of their name
and records the TRACE spelling separately in trace_name_key. The evidence loader
had two places that assumed the two were the same:

  - build_trace_rows joined professors_catalog on name_key, so a fuzzy-matched
    professor's TRACE comments matched nothing. An INNER JOIN, so they were
    dropped from the RAG corpus with no error — Ask simply stopped being able to
    cite them.
  - build_rmp_rows validated an RMP review's course code against the set of
    courses TRACE says the professor taught, but looked that set up under the RMP
    name, so it always came back empty and every course attribution was discarded.

The join is exercised against a real SQLite database rather than a canned result
set, because the fix lives in the SQL and a fake query_fn would assert nothing.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
from load_evidence_to_crdb import build_rmp_rows, build_trace_rows  # noqa: E402

LONG = "this comment is definitely longer than fifteen characters"


@pytest.fixture
def query_fn():
    """A query_fn over an in-memory stand-in for the four tables involved.

    Only the columns these two functions select are modelled. Rows come back as
    plain dicts because the build functions use both r["x"] and r.get("x").
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE professors_catalog (
            name_key TEXT, slug TEXT, trace_name_key TEXT);
        CREATE TABLE trace_courses (
            course_id INT, instructor_id INT, term_id INT,
            name_key TEXT, course_code TEXT);
        CREATE TABLE trace_comments (
            id INT, comment TEXT,
            tc_course_id INT, tc_instructor_id INT, tc_term_id INT);
        CREATE TABLE rmp_reviews (
            id INT, name_key TEXT, course TEXT, comment TEXT,
            quality INT, difficulty INT, tags TEXT, grade TEXT);
    """)
    # A fuzzy-matched professor: RMP calls them "dan koloski", TRACE "daniel
    # koloski". Their one catalog row is keyed by the RMP name.
    conn.execute("INSERT INTO professors_catalog VALUES (?,?,?)",
                 ("dan koloski", "dan-koloski", "daniel koloski"))
    # An exact match: trace_name_key IS NULL, the pre-existing majority case.
    conn.execute("INSERT INTO professors_catalog VALUES (?,?,?)",
                 ("jane doe", "jane-doe", None))
    conn.execute("INSERT INTO trace_courses VALUES (?,?,?,?,?)",
                 (1, 10, 100, "daniel koloski", "ANLY6500"))
    conn.execute("INSERT INTO trace_courses VALUES (?,?,?,?,?)",
                 (2, 20, 200, "jane doe", "CS2500"))
    conn.execute("INSERT INTO trace_comments VALUES (?,?,?,?,?)",
                 (1, f"koloski: {LONG}", 1, 10, 100))
    conn.execute("INSERT INTO trace_comments VALUES (?,?,?,?,?)",
                 (2, f"doe: {LONG}", 2, 20, 200))
    conn.commit()

    def run(sql, params=()):
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

    yield run
    conn.close()


# ── TRACE comments ──────────────────────────────────────────────────────────

def test_fuzzy_matched_professors_trace_comments_are_kept(query_fn):
    rows = build_trace_rows(query_fn)
    slugs = {r["professor_slug"] for r in rows}
    assert "dan-koloski" in slugs, \
        "a fuzzy-matched professor's TRACE comments must reach the evidence corpus"


def test_exact_matched_professors_are_unaffected(query_fn):
    rows = build_trace_rows(query_fn)
    assert "jane-doe" in {r["professor_slug"] for r in rows}


def test_every_trace_comment_is_carried_over(query_fn):
    rows = build_trace_rows(query_fn)
    assert len(rows) == 2, "neither professor's comment may be dropped by the join"


def test_trace_rows_carry_the_course_code(query_fn):
    by_slug = {r["professor_slug"]: r for r in build_trace_rows(query_fn)}
    assert by_slug["dan-koloski"]["course_code"] == "ANLY6500"


# ── RMP reviews ─────────────────────────────────────────────────────────────

def _add_rmp_review(query_fn, name_key, course):
    # query_fn holds the connection; reuse it to insert through the same handle.
    query_fn(
        "INSERT INTO rmp_reviews VALUES (1, ?, ?, ?, 4, 3, '', 'A')",
        (name_key, course, f"rmp: {LONG}"),
    )


def test_rmp_course_code_validates_against_the_trace_name(query_fn):
    # The review's course really is one the professor taught, but TRACE files it
    # under "daniel koloski" while the review is filed under "dan koloski".
    _add_rmp_review(query_fn, "dan koloski", "ANLY6500")
    rows = build_rmp_rows(query_fn)
    assert len(rows) == 1
    assert rows[0]["course_code"] == "ANLY6500", \
        "course attribution must survive the RMP/TRACE name difference"


def test_rmp_course_code_is_still_rejected_when_not_taught(query_fn):
    # The validation must stay a validation — an unrelated code is still dropped.
    _add_rmp_review(query_fn, "dan koloski", "PHIL1000")
    rows = build_rmp_rows(query_fn)
    assert rows[0]["course_code"] == "", "a course TRACE has no record of is not attributed"


def test_rmp_exact_match_still_validates(query_fn):
    _add_rmp_review(query_fn, "jane doe", "CS2500")
    rows = build_rmp_rows(query_fn)
    assert rows[0]["course_code"] == "CS2500"


def test_rmp_review_for_an_unknown_professor_is_skipped(query_fn):
    _add_rmp_review(query_fn, "nobody at all", "CS2500")
    assert build_rmp_rows(query_fn) == []
