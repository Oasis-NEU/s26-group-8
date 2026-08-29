"""Reviews deleted on RMP have to leave the table, without truncating it.

migrate_to_crdb.py only inserts (ON CONFLICT DO NOTHING), so a review removed
on RMP stayed in rmp_reviews and kept rendering — while the count beside it,
which precompute derives from the fresh CSV, had already dropped. The list and
the number described different sets.

The shipped fix was a monthly TRUNCATE + full reload. That converges, but it
regenerates every rmp_reviews.id, and evidence.source_ref for RMP *is* that
rowid (scraper/load_evidence_to_crdb.py:208) — so each full replace orphans the
RMP half of the RAG corpus and forces a re-embed. It also leaves the table
empty mid-load, since the TRUNCATE commits on its own.

Pruning deletes only the rows that actually went away, weekly, keeping ids
stable. Two safety rails, because this is the one code path that deletes
production rows: an empty key set is refused outright, and a prune larger than
MAX_PRUNE_PCT of the table needs --force.

No database: driven by a recording fake connection.
"""

import csv

import pytest

from Better_Scraper.scrape_guard import RELATIVE_FLOOR_PCT
from prune_rmp_reviews import (
    MAX_PRUNE_PCT,
    csv_keys,
    prune,
    stale_ids,
)

# The columns fetch_lite.py's dump_reviews_to_csv writes.
COLUMNS = ["professor_name", "department", "overall_rating", "course", "quality",
           "difficulty", "date", "tags", "attendance", "grade", "textbook",
           "online_class", "comment"]


def write_reviews(path, rows):
    """rows: (professor_name, course, date, comment) tuples.

    Written with csv.writer, not string formatting: RMP dates are
    "Jan 1st, 2025" and the embedded comma has to be quoted the way the real
    scraper quotes it.
    """
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for name, course, date, comment in rows:
            writer.writerow({"professor_name": name, "department": "CS",
                             "overall_rating": "4.0", "course": course,
                             "quality": "4.0", "difficulty": "2.0", "date": date,
                             "comment": comment})
    return path


class FakeConn:
    """Records executed SQL the way psycopg2 would deliver it.

    `has_evidence` models a database where the RAG corpus was never loaded: the
    probe statement raises the way CockroachDB does, and every later statement
    has to still run — a failed statement aborts the real transaction, which is
    why the probe is its own.
    """

    def __init__(self, rows, has_evidence=True, evidence_rowcount=0):
        self._rows = rows
        self.has_evidence = has_evidence
        self.evidence_rowcount = evidence_rowcount
        self.executed = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if "evidence" in flat and not self._conn.has_evidence:
            raise RuntimeError('relation "evidence" does not exist')
        self._conn.executed.append((flat, params))
        if flat.startswith("DELETE FROM evidence "):
            self.rowcount = self._conn.evidence_rowcount
        else:
            self.rowcount = 0

    def fetchall(self):
        return list(self._conn._rows)


# ── reading the fresh CSV ───────────────────────────────────────────────────

def test_csv_keys_reads_professor_course_and_date(tmp_path):
    path = write_reviews(tmp_path / "r.csv", [("Ann Lee", "CS2500", "Jan 1st, 2025", "good")])
    assert csv_keys(path) == {("Ann Lee", "CS2500", "Jan 1st, 2025")}


def test_csv_keys_collapses_rows_sharing_a_key(tmp_path):
    # Two students, same professor, course and day: one key, two reviews. The
    # 4-column unique constraint keeps both rows; the prune key does not
    # distinguish them, so both survive together.
    path = write_reviews(tmp_path / "r.csv", [
        ("Ann Lee", "CS2500", "Jan 1st, 2025", "first"),
        ("Ann Lee", "CS2500", "Jan 1st, 2025", "second"),
    ])
    assert len(csv_keys(path)) == 1


def test_csv_keys_on_header_only_file_is_empty(tmp_path):
    assert csv_keys(write_reviews(tmp_path / "r.csv", [])) == set()


# ── deciding what is stale ──────────────────────────────────────────────────

def test_row_present_in_the_csv_is_kept():
    fresh = {("Ann Lee", "CS2500", "Jan 1st, 2025")}
    rows = [(1, "Ann Lee", "CS2500", "Jan 1st, 2025")]
    assert stale_ids(rows, fresh) == []


def test_row_absent_from_the_csv_is_stale():
    fresh = {("Ann Lee", "CS2500", "Jan 1st, 2025")}
    rows = [(1, "Ann Lee", "CS2500", "Jan 1st, 2025"),
            (2, "Bob Roe", "CS3500", "Feb 2nd, 2025")]
    assert stale_ids(rows, fresh) == [2]


def test_rows_sharing_a_present_key_are_both_kept():
    # Under-pruning is the safe direction: keeping a deleted review for another
    # week is recoverable, deleting a live one is not.
    fresh = {("Ann Lee", "CS2500", "Jan 1st, 2025")}
    rows = [(1, "Ann Lee", "CS2500", "Jan 1st, 2025"),
            (2, "Ann Lee", "CS2500", "Jan 1st, 2025")]
    assert stale_ids(rows, fresh) == []


def test_null_course_and_date_are_matched_not_treated_as_stale():
    # RMP serves reviews with no course attached; the CSV writes them as "".
    fresh = {("Ann Lee", "", "")}
    rows = [(1, "Ann Lee", None, None)]
    assert stale_ids(rows, fresh) == []


def test_a_csv_row_missing_from_the_db_deletes_nothing():
    fresh = {("Ann Lee", "CS2500", "Jan 1st, 2025"),
             ("New Prof", "CS1800", "Mar 3rd, 2025")}
    rows = [(1, "Ann Lee", "CS2500", "Jan 1st, 2025")]
    assert stale_ids(rows, fresh) == []


# ── safety rails ────────────────────────────────────────────────────────────

def test_empty_csv_refuses_to_prune(tmp_path):
    # A header-only or truncated CSV would otherwise delete all 44.5k rows.
    path = write_reviews(tmp_path / "r.csv", [])
    conn = FakeConn([(1, "Ann Lee", "CS2500", "Jan 1st, 2025")])
    with pytest.raises(SystemExit) as exc:
        prune(conn, path)
    assert "0 keys" in str(exc.value)
    assert not any("DELETE" in sql for sql, _ in conn.executed)


def test_prune_larger_than_the_ceiling_is_refused(tmp_path):
    # 1 of 3 rows is 33%, well over MAX_PRUNE_PCT.
    path = write_reviews(tmp_path / "r.csv", [
        ("Ann Lee", "CS2500", "Jan 1st, 2025", "a"),
        ("Bob Roe", "CS3500", "Feb 2nd, 2025", "b"),
    ])
    conn = FakeConn([(1, "Ann Lee", "CS2500", "Jan 1st, 2025"),
                     (2, "Bob Roe", "CS3500", "Feb 2nd, 2025"),
                     (3, "Gone Prof", "CS9999", "Dec 9th, 2024")])
    with pytest.raises(SystemExit) as exc:
        prune(conn, path)
    assert str(MAX_PRUNE_PCT) in str(exc.value)
    assert not any("DELETE" in sql for sql, _ in conn.executed)


def test_ceiling_sits_below_the_scrape_guard_tolerance():
    """The two thresholds have to compose, not just each be reasonable alone.

    scrape_guard passes a scrape at RELATIVE_FLOOR_PCT of the previous week, so
    the rows a degraded-but-passing scrape may be missing is (100 -
    RELATIVE_FLOOR_PCT)% of the table — and the prune reads every missing row as
    a deletion on RMP. If the ceiling is at or above that, the whole band the
    guard waves through is a band the prune deletes without asking: two rails
    that each look sound and together stop nothing. They shipped at 5% and 98%,
    which is exactly that hole.
    """
    guard_tolerance_pct = 100 - RELATIVE_FLOOR_PCT
    assert MAX_PRUNE_PCT < guard_tolerance_pct, (
        f"a scrape may lose {guard_tolerance_pct}% of rows and still pass "
        f"scrape_guard, but the prune deletes up to {MAX_PRUNE_PCT}% without "
        "--force, so that loss is silently prunable"
    )


def test_force_overrides_the_ceiling(tmp_path):
    path = write_reviews(tmp_path / "r.csv", [("Ann Lee", "CS2500", "Jan 1st, 2025", "a")])
    conn = FakeConn([(1, "Ann Lee", "CS2500", "Jan 1st, 2025"),
                     (2, "Gone", "CS9999", "Dec 9th, 2024")])
    stats = prune(conn, path, force=True)
    assert stats["deleted"] == 1


# ── executing the delete ────────────────────────────────────────────────────

def test_stale_rows_are_deleted_by_id(tmp_path):
    # 1 stale in 200 rows is 0.5%, under the ceiling, so this exercises the
    # default path rather than --force.
    live = [(f"Prof {i}", f"CS{i}", "Jan 1st, 2025", "a") for i in range(199)]
    path = write_reviews(tmp_path / "r.csv", live)
    rows = [(i, name, course, date) for i, (name, course, date, _) in enumerate(live)]
    rows.append((999, "Gone", "CS9999", "Dec 9th, 2024"))
    conn = FakeConn(rows)
    stats = prune(conn, path)
    deletes = [(sql, params) for sql, params in conn.executed
               if sql.startswith("DELETE FROM rmp_reviews")]
    assert len(deletes) == 1
    assert deletes[0][1] == ([999],)
    assert stats["deleted"] == 1
    # One commit per delete batch, plus the evidence-table probe's own — it has
    # to be a separate transaction, since a missing table aborts the one it runs
    # in and would take the rmp_reviews DELETE down with it.
    assert conn.commits == 2


def test_nothing_stale_issues_no_delete(tmp_path):
    path = write_reviews(tmp_path / "r.csv", [("Ann Lee", "CS2500", "Jan 1st, 2025", "a")])
    conn = FakeConn([(1, "Ann Lee", "CS2500", "Jan 1st, 2025")])
    stats = prune(conn, path)
    assert stats["deleted"] == 0
    assert not any("DELETE" in sql for sql, _ in conn.executed)


def test_deletes_are_batched(tmp_path):
    path = write_reviews(tmp_path / "r.csv", [("Keep", "CS1", "Jan 1st, 2025", "a")] * 1)
    rows = [(1, "Keep", "CS1", "Jan 1st, 2025")]
    rows += [(i, "Gone", f"CS{i}", "Dec 9th, 2024") for i in range(2, 8)]
    conn = FakeConn(rows)
    stats = prune(conn, path, batch_size=2, force=True)
    deletes = [p for sql, p in conn.executed
               if sql.startswith("DELETE FROM rmp_reviews")]
    assert [len(p[0]) for p in deletes] == [2, 2, 2]
    assert stats["deleted"] == 6


def test_dry_run_reports_without_deleting(tmp_path):
    path = write_reviews(tmp_path / "r.csv", [("Ann Lee", "CS2500", "Jan 1st, 2025", "a")])
    conn = FakeConn([(1, "Ann Lee", "CS2500", "Jan 1st, 2025"),
                     (2, "Gone", "CS9999", "Dec 9th, 2024")])
    stats = prune(conn, path, dry_run=True, force=True)
    assert stats["stale"] == 1
    assert stats["deleted"] == 0
    assert not any("DELETE" in sql for sql, _ in conn.executed)
    assert conn.commits == 0


# ── the documented limit ────────────────────────────────────────────────────

def test_an_edited_comment_is_not_pruned(tmp_path):
    # The prune key is (professor_name, course, date), not the 4-column unique
    # constraint, so edited review text is left alone. Reading every comment
    # back to compare would mean a 16MB read each week, and RMP does not let a
    # student edit a posted rating — only moderation removes one.
    path = write_reviews(tmp_path / "r.csv", [("Ann Lee", "CS2500", "Jan 1st, 2025", "new text")])
    conn = FakeConn([(1, "Ann Lee", "CS2500", "Jan 1st, 2025")])
    assert prune(conn, path)["deleted"] == 0


# ── evidence cleanup ────────────────────────────────────────────────────────
#
# evidence.source_ref for RMP is the rmp_reviews rowid, so deleting a review
# without deleting its evidence row leaves the chat path able to retrieve and
# quote a review that no longer exists on the site or on the professor page —
# until the next full evidence rebuild happens to notice. That is the same
# orphaning the module docstring gives as the reason not to truncate.

def test_evidence_rows_go_with_the_reviews_they_quote(tmp_path):
    path = write_reviews(tmp_path / "r.csv", [("Ann Lee", "CS2500", "Jan 1st, 2025", "a")])
    conn = FakeConn([(1, "Ann Lee", "CS2500", "Jan 1st, 2025"),
                     (999, "Gone", "CS9999", "Dec 9th, 2024")],
                    evidence_rowcount=1)
    stats = prune(conn, path, force=True)

    ev = [(sql, params) for sql, params in conn.executed
          if sql.startswith("DELETE FROM evidence")]
    assert len(ev) == 2, "expected an embeddings delete and an evidence delete"
    # source_ref is TEXT, so the ids have to be strings. Passing ints matches
    # nothing and deletes none, silently.
    assert ev[0][1] == (["999"],)
    assert ev[1][1] == (["999"],)
    assert stats["evidence_deleted"] == 1


def test_embeddings_are_deleted_before_the_evidence_rows(tmp_path):
    """The other order leaves orphan vectors that no longer join to anything."""
    path = write_reviews(tmp_path / "r.csv", [("Ann Lee", "CS2500", "Jan 1st, 2025", "a")])
    conn = FakeConn([(1, "Ann Lee", "CS2500", "Jan 1st, 2025"),
                     (999, "Gone", "CS9999", "Dec 9th, 2024")])
    prune(conn, path, force=True)
    order = [sql for sql, _ in conn.executed if sql.startswith("DELETE")]
    assert order[0].startswith("DELETE FROM evidence_embeddings")
    assert order[1].startswith("DELETE FROM evidence ")
    assert order[2].startswith("DELETE FROM rmp_reviews")


def test_evidence_is_deleted_in_the_same_transaction_as_the_review(tmp_path):
    """A crash between the two is what produces the orphan we are avoiding."""
    path = write_reviews(tmp_path / "r.csv", [("Ann Lee", "CS2500", "Jan 1st, 2025", "a")])
    conn = FakeConn([(1, "Ann Lee", "CS2500", "Jan 1st, 2025"),
                     (999, "Gone", "CS9999", "Dec 9th, 2024")])
    prune(conn, path, force=True)
    # Probe commit, then exactly one more for the single batch — no commit
    # between the evidence deletes and the review delete.
    assert conn.commits == 2


def test_a_database_without_the_evidence_table_still_prunes(tmp_path):
    """The RAG corpus is a separate job; an install without it can still prune."""
    path = write_reviews(tmp_path / "r.csv", [("Ann Lee", "CS2500", "Jan 1st, 2025", "a")])
    conn = FakeConn([(1, "Ann Lee", "CS2500", "Jan 1st, 2025"),
                     (999, "Gone", "CS9999", "Dec 9th, 2024")],
                    has_evidence=False)
    stats = prune(conn, path, force=True)
    assert stats["deleted"] == 1
    assert stats["evidence_deleted"] == 0
    assert conn.rollbacks == 1, "the failed probe has to roll its transaction back"
    assert [sql for sql, _ in conn.executed if sql.startswith("DELETE")] == [
        "DELETE FROM rmp_reviews WHERE id = ANY(%s)"]


def test_nothing_stale_does_not_probe_for_evidence(tmp_path):
    """No deletes means no cleanup, so the probe is not worth a round trip."""
    path = write_reviews(tmp_path / "r.csv", [("Ann Lee", "CS2500", "Jan 1st, 2025", "a")])
    conn = FakeConn([(1, "Ann Lee", "CS2500", "Jan 1st, 2025")])
    prune(conn, path)
    assert not any("evidence" in sql for sql, _ in conn.executed)


def test_dry_run_touches_no_evidence(tmp_path):
    path = write_reviews(tmp_path / "r.csv", [("Ann Lee", "CS2500", "Jan 1st, 2025", "a")])
    conn = FakeConn([(1, "Ann Lee", "CS2500", "Jan 1st, 2025"),
                     (999, "Gone", "CS9999", "Dec 9th, 2024")])
    stats = prune(conn, path, dry_run=True, force=True)
    assert stats["deleted"] == 0
    assert not any(sql.startswith("DELETE") for sql, _ in conn.executed)
