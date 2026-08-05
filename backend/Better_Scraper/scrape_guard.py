"""Gate between a finished RMP scrape and the writes that can't be undone.

Everything downstream of the scrape is destructive in one direction or another:
precompute drops and rebuilds professors_catalog from these CSVs, and the
workflow force-pushes them over the data store as an orphan commit. So the last
chance to notice a bad scrape is here, before either happens.

Two checks:

  Completeness — precompute reads all six CSVs and crashes mid-run on a missing
  one, which is a worse failure than stopping now. trace_comments may ship as
  .csv or .zip (precompute falls back), so either satisfies it.

  Size — the scrape must not have shrunk. Absolute floors alone don't hold up:
  the original `< 3000` professor floor was set against a normal haul of 3,892,
  which is four pages of 1,000, so losing the final 892-row page left exactly
  3,000 and passed by one row. And the gap widens as the school grows — at 5,000
  professors a whole lost page still clears 3,000. The workflow clones the
  previous snapshot before scraping, so the honest comparison is against what
  the store actually held. The absolute floors stay as a backstop for the first
  run, when there is no baseline.

Usage (see .github/workflows/data-refresh.yml):
    python scrape_guard.py baseline --data-dir DIR --out baseline.json
    python scrape_guard.py check    --data-dir DIR --baseline baseline.json
"""

import argparse
import csv
import json
import os
import sys
from typing import Dict, List, Optional, Sequence

# Files precompute reads. trace_comments is handled separately — either form.
REQUIRED_FILES: Sequence[str] = (
    "rmp_professors.csv",
    "rmp_reviews.csv",
    "trace_courses.csv",
    "trace_scores.csv",
    "professor_photos.csv",
)
TRACE_COMMENTS_ALTERNATIVES: Sequence[str] = ("trace_comments.csv", "trace_comments.zip")

# The files this scrape rewrites, and the counts worth guarding.
COUNTED_FILES: Sequence[str] = ("rmp_professors.csv", "rmp_reviews.csv")

# Backstop for a run with no baseline. Roughly 70-80% of a normal haul.
ABSOLUTE_FLOORS: Dict[str, int] = {
    "rmp_professors.csv": 3000,
    "rmp_reviews.csv": 30000,
}

# How far below the previous snapshot a healthy run can land. Measured drift
# between two real consecutive scrapes was +0.8% professors and +4.4% reviews,
# with 52 of 42,673 reviews (0.12%) deleted upstream, so 2% is loose enough for
# real churn and far tighter than any lost page.
RELATIVE_FLOOR_PCT: float = 98.0


def count_rows(path: str) -> int:
    """Data rows in a CSV, excluding the header.

    Uses the csv reader rather than counting lines: RMP review text carries
    newlines inside quoted fields, so `wc -l` over-counts rmp_reviews.csv.
    """
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        try:
            next(reader)
        except StopIteration:
            return 0                      # header-only or empty file
        return sum(1 for _ in reader)


def read_baseline(path: Optional[str]) -> Dict[str, int]:
    """Previous snapshot counts. Missing or unreadable reads as "no baseline",
    which downgrades to the absolute floors rather than blocking the run."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: int(v) for k, v in data.items()}
    except (ValueError, TypeError, OSError):
        return {}


def write_baseline(data_dir: str, out_path: str) -> Dict[str, int]:
    """Record what the data store held before the scrape overwrites it."""
    counts = {name: count_rows(os.path.join(data_dir, name)) for name in COUNTED_FILES}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(counts, f)
    return counts


def problems(data_dir: str, baseline: Dict[str, int]) -> List[str]:
    """Every reason this scrape should not reach the DB or the data store."""
    found: List[str] = []

    for name in REQUIRED_FILES:
        if not os.path.exists(os.path.join(data_dir, name)):
            found.append(f"Missing required data file: {name}")
    if not any(os.path.exists(os.path.join(data_dir, n))
               for n in TRACE_COMMENTS_ALTERNATIVES):
        found.append("Missing trace_comments (.csv or .zip)")

    for name in COUNTED_FILES:
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            continue                      # already reported as missing
        count = count_rows(path)

        floor = ABSOLUTE_FLOORS.get(name)
        if floor is not None and count < floor:
            found.append(
                f"{name}: {count:,} rows is below the absolute floor of {floor:,}"
            )
            continue                      # one complaint per file is enough

        previous = baseline.get(name, 0)
        if previous > 0 and count * 100 < previous * RELATIVE_FLOOR_PCT:
            shrink = 100.0 - (count * 100.0 / previous)
            found.append(
                f"{name}: {count:,} rows is {shrink:.1f}% below the {previous:,} "
                f"in the previous snapshot (tolerance {100 - RELATIVE_FLOOR_PCT:.0f}%)"
            )

    return found


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("baseline", help="record the pre-scrape row counts")
    b.add_argument("--data-dir", required=True)
    b.add_argument("--out", required=True)

    c = sub.add_parser("check", help="verify the post-scrape output")
    c.add_argument("--data-dir", required=True)
    c.add_argument("--baseline")

    args = parser.parse_args(argv)

    if args.command == "baseline":
        counts = write_baseline(args.data_dir, args.out)
        print("Baseline (previous snapshot): "
              + ", ".join(f"{k} {v:,}" for k, v in counts.items()))
        return 0

    baseline = read_baseline(args.baseline)
    if args.baseline and not baseline:
        print("No usable baseline — falling back to absolute floors only.")

    for name in COUNTED_FILES:
        print(f"  {name}: {count_rows(os.path.join(args.data_dir, name)):,} rows")

    found = problems(args.data_dir, baseline)
    if found:
        for p in found:
            print(f"::error::{p}. Aborting before DB writes to protect existing data.")
        return 1

    print("Scrape looks complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
