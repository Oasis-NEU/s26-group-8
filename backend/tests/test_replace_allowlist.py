"""--replace must refuse rmp_reviews, whatever the caller asks for.

Truncating rmp_reviews regenerates every id, and evidence.source_ref for RMP is
that rowid (scraper/load_evidence_to_crdb.py:208) — a full replace orphans the
RMP half of the RAG corpus and forces a re-embed. It also empties a table the
site reads on every professor page, because the TRUNCATE commits before the
reload starts. prune_rmp_reviews.py converges the table without either cost, so
nothing needs the truncate any more and the workflow no longer asks for it.

The allowlist is the enforcement point: the workflow is one caller, and a hand-run
`python migrate_to_crdb.py rmp_reviews --replace` has to fail too.

rmp_professors stays replaceable — 3,889 rows of aggregates RMP recomputes
constantly, no request path reads the table, and nothing references its ids.
"""

from migrate_to_crdb import REPLACE_ALLOWED, TABLES


def test_rmp_reviews_cannot_be_replaced():
    assert "rmp_reviews" not in REPLACE_ALLOWED


def test_rmp_professors_can_be_replaced():
    assert "rmp_professors" in REPLACE_ALLOWED


def test_cumulative_artifacts_cannot_be_replaced():
    # TRACE and photo CSVs accumulate across scrapes rather than being a
    # complete snapshot of the source, so replacing from them destroys data.
    for table in ("trace_courses", "trace_scores", "trace_comments",
                  "professor_photos"):
        assert table not in REPLACE_ALLOWED


def test_allowlist_only_names_real_tables():
    assert REPLACE_ALLOWED <= set(TABLES)
