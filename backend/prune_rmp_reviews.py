"""Delete rmp_reviews rows that no longer exist on RateMyProfessors.

migrate_to_crdb.py inserts with ON CONFLICT DO NOTHING and never deletes, so a
review removed on RMP stayed in the table and kept rendering — while the count
beside it, which precompute derives from the fresh CSV, had already dropped.
The list and the number described different sets.

Truncate-and-reload also converges, but it regenerates every rmp_reviews.id,
and evidence.source_ref for RMP *is* that rowid
(scraper/load_evidence_to_crdb.py:208) — so each full replace orphans the RMP
half of the RAG corpus and forces a re-embed. It also leaves the table empty
mid-load, because the TRUNCATE commits by itself. Pruning deletes only the rows
that actually went away, weekly, and leaves ids alone.

Deleting one review still orphans its own evidence row, for the same reason a
truncate orphans all of them, so each batch deletes the matching evidence rows
and embeddings in the same transaction. Without that the chat path goes on
retrieving and quoting a review that is gone from the site and from the
professor page, until the next full evidence rebuild happens to notice.

Runs between the RMP load and precompute, so the reviews and the counts
precompute writes describe the same set.

Match key is (professor_name, course, date), not the 4-column unique
constraint: comparing comment text would mean reading all ~16MB of it back each
week, and RMP gives students no way to edit a posted rating — only moderation
removes one. Rows sharing a key therefore survive together, which under-prunes
rather than over-prunes.

Usage:
    python prune_rmp_reviews.py [--csv PATH] [--dry-run] [--force]
"""

import argparse
import csv
import os
import sys

DEFAULT_CSV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "Better_Scraper", "output_data", "rmp_reviews.csv",
)

# Rows per DELETE. Matches migrate_to_crdb.py's BATCH_SIZE rationale: stay
# under CockroachDB's lock-tracking budget for a single statement.
BATCH_SIZE = 5000

# A prune bigger than this share of the table means the CSV is wrong, not that
# RMP deleted that many reviews. The weekly delta is tens of rows (~0.1%).
#
# The ceiling has to sit BELOW what scrape_guard tolerates, or it never fires on
# the case it exists for. scrape_guard passes a scrape at RELATIVE_FLOOR_PCT
# (98%) of the previous week, so a degraded-but-passing scrape can arrive missing
# 2% of its rows — ~890 on 44.5k — and the prune reads every one of them as
# "deleted on RMP". At a 5% ceiling that whole band was silently prunable: the
# guard said yes and the ceiling said yes. Below 2%, a scrape bad enough to clear
# the guard on a technicality stops here instead, and the operator decides.
#
# 1% keeps ~445 rows of headroom on the current table, ~9x the observed weekly
# delta. test_ceiling_sits_below_the_scrape_guard_tolerance pins the relationship
# so the two constants cannot drift back apart.
MAX_PRUNE_PCT = 1

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def csv_keys(csv_path):
    """The (professor_name, course, date) keys present in the fresh CSV."""
    keys = set()
    with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            keys.add((
                row.get("professor_name") or "",
                row.get("course") or "",
                row.get("date") or "",
            ))
    return keys


def stale_ids(db_rows, fresh_keys):
    """Ids in `db_rows` whose key is absent from `fresh_keys`.

    db_rows: (id, professor_name, course, date) tuples. NULL course/date are
    normalised to "" to match the CSV, which writes empty strings.
    """
    stale = []
    for row_id, name, course, date in db_rows:
        key = (name or "", course or "", date or "")
        if key not in fresh_keys:
            stale.append(row_id)
    return stale


def has_evidence_table(conn):
    """Is the RAG evidence corpus present in this database?

    Probed once, in its own transaction, rather than by catching the failure
    inside the delete loop: on CockroachDB a failed statement aborts the whole
    transaction, so a missing table would take the rmp_reviews DELETE down with
    it and the prune would do nothing at all.

    The corpus is built by a separate job (scraper/load_evidence_to_crdb.py), and
    an install that has never run it should still be able to prune reviews.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM evidence LIMIT 1")
            cur.fetchall()
        conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - any failure here means "do not try"
        conn.rollback()
        print(f"  evidence table unavailable, skipping evidence cleanup ({exc})")
        return False


def _delete_evidence(cur, review_ids):
    """Delete the evidence rows (and embeddings) quoting `review_ids`.

    Returns how many evidence rows went. source_ref is TEXT — load_evidence_to_crdb
    writes `str(source_ref)` — so the ids are stringified rather than passed as
    ints, which would match nothing and silently delete none.

    Embeddings first: evidence_embeddings.evidence_id refers to the row about to
    be deleted, so the other order leaves orphan vectors that no longer join to
    anything and cannot be found again to clean up.
    """
    refs = [str(rid) for rid in review_ids]
    cur.execute(
        "DELETE FROM evidence_embeddings WHERE evidence_id IN "
        "(SELECT id FROM evidence WHERE source = 'rmp' AND source_ref = ANY(%s))",
        (refs,),
    )
    cur.execute(
        "DELETE FROM evidence WHERE source = 'rmp' AND source_ref = ANY(%s)",
        (refs,),
    )
    return cur.rowcount or 0


def prune(conn, csv_path, batch_size=BATCH_SIZE, dry_run=False, force=False):
    """Delete rmp_reviews rows absent from `csv_path`. Returns stats."""
    fresh = csv_keys(csv_path)
    if not fresh:
        sys.exit(
            f"prune: {csv_path} yielded 0 keys; refusing to delete every row. "
            "A header-only or truncated CSV means the scrape failed."
        )

    with conn.cursor() as cur:
        cur.execute("SELECT id, professor_name, course, date FROM rmp_reviews")
        db_rows = cur.fetchall()

    total = len(db_rows)
    stale = stale_ids(db_rows, fresh)
    print(f"  {total} rows in table, {len(fresh)} keys in CSV, {len(stale)} stale")

    if stale and not force:
        pct = len(stale) * 100.0 / total if total else 0.0
        if pct > MAX_PRUNE_PCT:
            sys.exit(
                f"prune: {len(stale)} of {total} rows ({pct:.1f}%) would be "
                f"deleted, over the {MAX_PRUNE_PCT}% ceiling. Investigate the "
                "CSV, then re-run with --force if the drop is real."
            )

    if dry_run:
        print(f"  Dry run — {len(stale)} rows left in place")
        return {"total": total, "stale": len(stale), "deleted": 0}

    prune_evidence = has_evidence_table(conn) if stale else False

    deleted = 0
    evidence_deleted = 0
    for start in range(0, len(stale), batch_size):
        batch = stale[start:start + batch_size]
        with conn.cursor() as cur:
            # Evidence first, in the same transaction as the review it quotes.
            #
            # evidence.source_ref for RMP is the rmp_reviews rowid, so deleting a
            # review without this leaves its evidence row and embedding behind,
            # and the chat path goes on retrieving and quoting a review that is
            # gone from the site and from the professor page. That is the same
            # orphaning this module's docstring gives as the reason not to
            # truncate — just one row at a time instead of all of them.
            #
            # Before the reviews, not after: the reverse order leaves exactly
            # those orphans if the run dies in between, which is the state we are
            # trying not to produce. Both in one transaction, so a failure rolls
            # back to a table where the two still agree.
            if prune_evidence:
                evidence_deleted += _delete_evidence(cur, batch)
            cur.execute("DELETE FROM rmp_reviews WHERE id = ANY(%s)", (batch,))
        conn.commit()
        deleted += len(batch)
        print(f"  Deleted {deleted}/{len(stale)}...", end="\r")

    if deleted:
        print(f"  Deleted {deleted} stale reviews")
        if prune_evidence:
            print(f"  Deleted {evidence_deleted} matching evidence rows "
                  f"(and their embeddings)")
    else:
        print("  Nothing to prune")
    return {"total": total, "stale": len(stale), "deleted": deleted,
            "evidence_deleted": evidence_deleted}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Fresh rmp_reviews.csv")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be deleted, delete nothing")
    parser.add_argument("--force", action="store_true",
                        help=f"Allow a prune over {MAX_PRUNE_PCT}%% of the table")
    args = parser.parse_args(argv)

    if not os.path.exists(args.csv):
        sys.exit(f"prune: {args.csv} not found; refusing to touch rmp_reviews.")

    # Imported here so the pure functions above stay testable without psycopg2
    # and without the module reading DATABASE_URL at import time.
    from migrate_to_crdb import get_connection

    conn = get_connection()
    try:
        prune(conn, args.csv, dry_run=args.dry_run, force=args.force)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
