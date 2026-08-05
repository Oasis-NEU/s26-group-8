"""Remove RMP reviews from the DB that RMP no longer serves.

migrate_to_crdb only inserts, so rmp_reviews grows monotonically while the CSV
it is loaded from is a fresh snapshot each week. That splits the profile page in
two: the "Ratings" count comes from the CSV (precompute.apply_counted_num_ratings
counts the rows we actually hold) while the review list and comment counts come
from this table (server.py, professor_full.py). A student deleting a review
lowers the count and leaves the text on the page forever. Measured across two
real consecutive scrapes, 52 of 42,673 reviews vanished upstream in one interval
— a few thousand orphans a year at the weekly cadence.

Deleting is the risky direction, so the scope is narrow by construction:

  Only professors present in the fresh CSV are considered. A professor whose
  review fetch failed contributes no rows to the CSV, so they fall out of scope
  automatically and keep everything they have. That is what makes this safe to
  run alongside the proportional failure tolerance in fetch_lite — no manifest
  of failed professors has to be threaded through.

  Values are compared with whitespace collapsed and NULL treated as "", so rows
  loaded before fetch_lite normalised comment text match their CSV counterpart
  instead of looking deleted. The conservative direction: a near-duplicate is
  kept, never removed on a formatting difference.

  A percentage cap (default 2% of the table) aborts the run rather than
  deleting, so a scrape that slipped past scrape_guard cannot empty the table.

Run:  python backend/prune_rmp_reviews.py [--dry-run] [--max-pct 2.0]
"""

import argparse
import csv
import os
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

CSVRow = Tuple[str, str, str, str]          # professor_name, course, date, comment
DBRow = Tuple[int, str, str, str, str]      # id + the same four

DEFAULT_MAX_PCT = 2.0
DEFAULT_CHUNK = 5000

# csv.field_size_limit defaults below the longest RMP comment on some builds.
csv.field_size_limit(10 * 1024 * 1024)


class PruneAborted(Exception):
    """The deletion was larger than the cap allows, so nothing was deleted."""


def _norm(value: Optional[str]) -> str:
    """Collapse whitespace and treat NULL as the empty string migrate writes."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def _key(name: str, course: str, date: str, comment: str) -> CSVRow:
    return (_norm(name), _norm(course), _norm(date), _norm(comment))


def load_csv_rows(path: str) -> List[CSVRow]:
    """The four identifying columns of every review in the fresh scrape."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found — refusing to prune. An empty read would put every "
            "professor out of scope and silently do nothing, hiding a broken run."
        )
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        return [
            (r.get("professor_name") or "", r.get("course") or "",
             r.get("date") or "", r.get("comment") or "")
            for r in csv.DictReader(f)
        ]


def rows_to_delete(csv_rows: Iterable[CSVRow], db_rows: Iterable[DBRow]) -> List[int]:
    """DB row ids for reviews RMP no longer serves.

    In scope: rows whose professor appears in the fresh CSV. Out of scope rows
    are left alone whatever they contain — see the module docstring.
    """
    in_scope: set = set()
    present: set = set()
    for name, course, date, comment in csv_rows:
        in_scope.add(_norm(name))
        present.add(_key(name, course, date, comment))

    doomed: List[int] = []
    for row_id, name, course, date, comment in db_rows:
        if _norm(name) not in in_scope:
            continue
        if _key(name, course, date, comment) not in present:
            doomed.append(row_id)
    return doomed


def exceeds_cap(deleting: int, total: int, max_pct: float) -> bool:
    """True when the deletion is too large a share of the table to trust."""
    if total <= 0:
        return False
    return deleting * 100.0 > total * max_pct


def prune(conn, csv_rows: Sequence[CSVRow], max_pct: float = DEFAULT_MAX_PCT,
          dry_run: bool = False, chunk_size: int = DEFAULT_CHUNK) -> int:
    """Delete orphaned reviews. Returns how many rows were (or would be) removed.

    Raises PruneAborted without deleting anything if the cap is exceeded.
    """
    cur = conn.cursor()
    cur.execute("SELECT id, professor_name, course, date, comment FROM rmp_reviews")
    db_rows = cur.fetchall()
    total = len(db_rows)

    doomed = rows_to_delete(csv_rows, db_rows)
    print(f"  {total:,} reviews in the DB, {len(csv_rows):,} in the fresh CSV")
    print(f"  {len(doomed):,} no longer served by RMP")

    if exceeds_cap(len(doomed), total, max_pct):
        raise PruneAborted(
            f"{len(doomed):,} of {total:,} rows ({len(doomed) * 100.0 / total:.1f}%) "
            f"would be deleted, over the {max_pct}% cap. Nothing was deleted. "
            "Either the scrape is incomplete or the cap needs raising deliberately."
        )

    if not doomed:
        print("  Nothing to prune.")
        return 0

    if dry_run:
        print(f"  Dry run — would delete {len(doomed):,} rows.")
        return len(doomed)

    for i in range(0, len(doomed), chunk_size):
        chunk = doomed[i:i + chunk_size]
        placeholders = ",".join(["%s"] * len(chunk))
        cur.execute(f"DELETE FROM rmp_reviews WHERE id IN ({placeholders})", chunk)
    conn.commit()
    print(f"  Deleted {len(doomed):,} rows.")
    return len(doomed)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be deleted, change nothing")
    parser.add_argument("--max-pct", type=float, default=DEFAULT_MAX_PCT,
                        help=f"abort above this share of the table (default {DEFAULT_MAX_PCT})")
    parser.add_argument("--csv", help="path to rmp_reviews.csv")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    database_url = os.getenv("CRDB_DATABASE_URL")
    if not database_url:
        sys.exit("Missing CRDB_DATABASE_URL in backend/.env")

    import psycopg2

    csv_path = args.csv or os.path.join(
        os.path.dirname(__file__), "Better_Scraper", "output_data", "rmp_reviews.csv")
    csv_rows = load_csv_rows(csv_path)

    conn = psycopg2.connect(database_url, sslmode="require")
    try:
        prune(conn, csv_rows, max_pct=args.max_pct, dry_run=args.dry_run)
    except PruneAborted as e:
        print(f"::error::{e}")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
