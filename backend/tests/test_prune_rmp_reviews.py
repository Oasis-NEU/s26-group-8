"""Reviews deleted on RMP must stop being served.

Two sources feed the same part of the UI. professors_catalog.num_ratings is
counted from the fresh CSV (precompute.apply_counted_num_ratings), while the
rendered review list and the comment counts read rmp_reviews in the DB
(server.py, professor_full.py). migrate_to_crdb only ever inserts, so a review
a student deletes on RMP leaves the CSV and stays in the DB forever: the count
drops, the list doesn't. Measured against two real consecutive scrapes, 52 of
42,673 reviews disappeared upstream in one interval.

The dangerous half of fixing that is deleting too much, so the scope is
deliberately narrow: only professors who appear in the fresh CSV are touched.
A professor whose review fetch failed contributes no CSV rows at all, so they
fall outside the scope automatically and keep everything — no failure manifest
needed. A percentage cap backstops the rest.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
import prune_rmp_reviews as P  # noqa: E402


def _db(*rows):
    """DB rows as (id, professor_name, course, date, comment)."""
    return list(rows)


def _csv(*rows):
    """CSV rows as (professor_name, course, date, comment)."""
    return list(rows)


# ── the drift this exists to fix ────────────────────────────────────────────

def test_a_review_deleted_upstream_is_pruned():
    db = _db((1, "Ann Lee", "CS1000", "2026-01-01", "still here"),
             (2, "Ann Lee", "CS1000", "2025-06-02", "deleted on rmp"))
    csv = _csv(("Ann Lee", "CS1000", "2026-01-01", "still here"))
    assert P.rows_to_delete(csv, db) == [2]


def test_reviews_still_on_rmp_are_kept():
    db = _db((1, "Ann Lee", "CS1000", "2026-01-01", "a"),
             (2, "Ann Lee", "CS2000", "2026-01-02", "b"))
    csv = _csv(("Ann Lee", "CS1000", "2026-01-01", "a"),
               ("Ann Lee", "CS2000", "2026-01-02", "b"))
    assert P.rows_to_delete(csv, db) == []


def test_two_reviews_sharing_course_and_date_are_matched_by_comment():
    # 4 such pairs exist in the real 44,536-row CSV.
    db = _db((1, "Ann Lee", "CS1000", "2026-01-01", "first"),
             (2, "Ann Lee", "CS1000", "2026-01-01", "second"))
    csv = _csv(("Ann Lee", "CS1000", "2026-01-01", "first"))
    assert P.rows_to_delete(csv, db) == [2]


# ── the scope guard: a failed fetch must not look like a mass deletion ──────

def test_a_professor_absent_from_the_csv_keeps_every_review():
    # This is what a failed review fetch looks like: zero CSV rows for them.
    # Pruning by "not in the CSV" alone would wipe the professor's whole page.
    db = _db((1, "Ann Lee", "CS1000", "2026-01-01", "a"),
             (2, "Bob Roe", "CS2000", "2026-01-02", "b"),
             (3, "Bob Roe", "CS2000", "2026-01-03", "c"))
    csv = _csv(("Ann Lee", "CS1000", "2026-01-01", "a"))
    assert P.rows_to_delete(csv, db) == []


def test_scope_is_per_professor_not_all_or_nothing():
    # Ann was re-scraped and lost one review; Bob's fetch failed. Prune Ann only.
    db = _db((1, "Ann Lee", "CS1000", "2026-01-01", "keep"),
             (2, "Ann Lee", "CS1000", "2025-01-01", "gone"),
             (3, "Bob Roe", "CS2000", "2026-01-02", "untouched"))
    csv = _csv(("Ann Lee", "CS1000", "2026-01-01", "keep"))
    assert P.rows_to_delete(csv, db) == [2]


def test_namesakes_reviews_are_pooled_under_the_shared_name():
    # 49 scraped professors share a name with a different RMP profile page.
    # Both profiles' reviews carry the same professor_name in both CSV and DB,
    # so neither namesake's rows can prune the other's.
    db = _db((1, "Rick Arrowood", "MGT1000", "2026-01-01", "profile one"),
             (2, "Rick Arrowood", "ENT2000", "2026-01-02", "profile two"))
    csv = _csv(("Rick Arrowood", "MGT1000", "2026-01-01", "profile one"),
               ("Rick Arrowood", "ENT2000", "2026-01-02", "profile two"))
    assert P.rows_to_delete(csv, db) == []


# ── value normalisation ─────────────────────────────────────────────────────

def test_null_and_empty_string_compare_equal():
    # migrate_to_crdb writes "" for a missing field; older rows may hold NULL.
    db = _db((1, "Ann Lee", None, "2026-01-01", None))
    csv = _csv(("Ann Lee", "", "2026-01-01", ""))
    assert P.rows_to_delete(csv, db) == []


def test_whitespace_differences_do_not_count_as_a_deletion():
    # fetch_lite collapses whitespace with " ".join(split()); rows loaded before
    # that would otherwise all look deleted and blow the cap every week.
    db = _db((1, "Ann Lee", "CS1000", "2026-01-01", "great  class\nreally"))
    csv = _csv(("Ann Lee", "CS1000", "2026-01-01", "great class really"))
    assert P.rows_to_delete(csv, db) == []


def test_professor_name_whitespace_is_normalised_for_scope():
    db = _db((1, "Ann  Lee", "CS1000", "2025-01-01", "gone"))
    csv = _csv(("Ann Lee", "CS1000", "2026-01-01", "keep"))
    assert P.rows_to_delete(csv, db) == [1], "same professor, so in scope"


# ── the cap ─────────────────────────────────────────────────────────────────

def test_deleting_within_the_cap_is_allowed():
    assert P.exceeds_cap(deleting=50, total=44536, max_pct=2.0) is False


def test_deleting_past_the_cap_is_refused():
    assert P.exceeds_cap(deleting=5000, total=44536, max_pct=2.0) is True


def test_the_cap_never_blocks_an_empty_table():
    assert P.exceeds_cap(deleting=0, total=0, max_pct=2.0) is False


def test_a_realistic_weeks_drift_is_well_inside_the_cap():
    # 52 deletions measured between two real scrapes.
    assert P.exceeds_cap(deleting=52, total=44536, max_pct=2.0) is False


# ── execution against a recording cursor (no live DB) ───────────────────────

class FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        if params and "DELETE" in sql.upper():
            self.rowcount = len(params)

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows):
        self.cursor_obj = FakeCursor(rows)
        self.commits = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def _rows(n, prefix="gone"):
    return [(i, "Ann Lee", "CS1000", f"2025-01-{i:02d}", f"{prefix} {i}")
            for i in range(1, n + 1)]


def test_prune_issues_a_delete_and_commits():
    conn = FakeConn(_rows(3) + [(99, "Ann Lee", "CS1000", "2026-01-01", "keep")])
    csv = _csv(("Ann Lee", "CS1000", "2026-01-01", "keep"))
    deleted = P.prune(conn, csv, max_pct=100.0)
    assert deleted == 3
    assert any("DELETE" in sql for sql, _ in conn.cursor_obj.executed)
    assert conn.commits == 1


def test_prune_deletes_in_chunks():
    conn = FakeConn(_rows(250))
    deleted = P.prune(conn, _csv(), max_pct=100.0, chunk_size=100)
    assert deleted == 0, "no professor in the CSV means nothing is in scope"
    assert not any("DELETE" in sql for sql, _ in conn.cursor_obj.executed)


def test_prune_chunks_a_large_in_scope_deletion():
    conn = FakeConn(_rows(250) + [(999, "Ann Lee", "CS1000", "2026-06-01", "keep")])
    csv = _csv(("Ann Lee", "CS1000", "2026-06-01", "keep"))
    deleted = P.prune(conn, csv, max_pct=100.0, chunk_size=100)
    assert deleted == 250
    deletes = [p for sql, p in conn.cursor_obj.executed if "DELETE" in sql]
    assert [len(p) for p in deletes] == [100, 100, 50]


def test_prune_refuses_and_does_not_delete_when_the_cap_is_exceeded():
    conn = FakeConn(_rows(100) + [(999, "Ann Lee", "CS1000", "2026-06-01", "keep")])
    csv = _csv(("Ann Lee", "CS1000", "2026-06-01", "keep"))
    with pytest.raises(P.PruneAborted) as e:
        P.prune(conn, csv, max_pct=2.0)
    assert "100" in str(e.value)
    assert not any("DELETE" in sql for sql, _ in conn.cursor_obj.executed)
    assert conn.commits == 0


def test_dry_run_reports_without_deleting():
    conn = FakeConn(_rows(3) + [(99, "Ann Lee", "CS1000", "2026-01-01", "keep")])
    csv = _csv(("Ann Lee", "CS1000", "2026-01-01", "keep"))
    assert P.prune(conn, csv, max_pct=100.0, dry_run=True) == 3
    assert not any("DELETE" in sql for sql, _ in conn.cursor_obj.executed)
    assert conn.commits == 0


def test_nothing_to_prune_skips_the_delete_entirely():
    conn = FakeConn([(1, "Ann Lee", "CS1000", "2026-01-01", "keep")])
    csv = _csv(("Ann Lee", "CS1000", "2026-01-01", "keep"))
    assert P.prune(conn, csv, max_pct=2.0) == 0
    assert not any("DELETE" in sql for sql, _ in conn.cursor_obj.executed)


# ── CSV loading ─────────────────────────────────────────────────────────────

def test_csv_rows_are_read_as_comparison_tuples(tmp_path):
    p = tmp_path / "rmp_reviews.csv"
    p.write_text(
        "professor_name,department,overall_rating,course,quality,difficulty,"
        "date,tags,attendance,grade,textbook,online_class,comment\n"
        "Ann Lee,CS,4.9,CS1000,5,3,2026-01-01,,Mandatory,,No,No,nice\n",
        encoding="utf-8",
    )
    assert P.load_csv_rows(str(p)) == [("Ann Lee", "CS1000", "2026-01-01", "nice")]


def test_missing_csv_is_an_error_not_an_empty_prune(tmp_path):
    # An empty read would put every professor out of scope, which is safe, but
    # silently doing nothing hides a broken run.
    with pytest.raises(FileNotFoundError):
        P.load_csv_rows(str(tmp_path / "nope.csv"))
