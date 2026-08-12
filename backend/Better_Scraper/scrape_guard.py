"""Data-refresh guardrails: absolute + relative row-count floors.

A broken or rate-limited scrape must not overwrite good data. These floors ran
as a bash block inside data-refresh.yml, where nothing exercised them until the
weekly cron fired. They live here so ci.yml's pytest job covers them.

Two layers, both checked before any DB or data-store write:

  absolute  Catches catastrophic failures (a 403-blocked scrape writing a
            header-only CSV) and covers the first run, when there is no
            baseline to compare against. Set at ~80% of the healthy count.

  relative  Catches degraded scrapes that clear the absolute floor anyway:
            3,100 professors beats the 3,000 floor and would then force-push
            over the only copy of good data. 98% of last week's store, applied
            to every file rather than just the RMP pair.

Stdlib only, deliberately: the workflow installs requirements-data.txt (which
has pandas) but ci.yml's test job installs requirements.txt (which does not),
and one code path that CI actually runs beats a faster one it never touches.

Usage:
    python Better_Scraper/scrape_guard.py baseline --data-dir DIR --out FILE
    python Better_Scraper/scrape_guard.py check --data-dir DIR [--baseline FILE]
                                               [--accept-lower]
"""

import argparse
import csv
import json
import os
import sys
import zipfile

# Logical name -> the filenames that may hold it, in preference order.
# trace_comments ships zipped: uncompressed it is ~415MB, over GitHub's 100MB
# limit, and precompute.py reads the .zip directly.
FILES = {
    "rmp_professors": ("rmp_professors.csv",),
    "rmp_reviews": ("rmp_reviews.csv",),
    "trace_courses": ("trace_courses.csv",),
    "trace_scores": ("trace_scores.csv",),
    "professor_photos": ("professor_photos.csv",),
    "trace_comments": ("trace_comments.csv", "trace_comments.zip"),
}

# ~80% of the healthy counts measured on the 2026-08-09 store (3,889 /
# 44,508 / 99,784 / 813,731 / 2,855 / 1,529,226). The first two match the
# floors that shipped in data-refresh.yml, so this is not a tightening.
ABSOLUTE_FLOORS = {
    "rmp_professors": 3000,
    "rmp_reviews": 30000,
    "trace_courses": 80000,
    "trace_scores": 650000,
    "professor_photos": 2200,
    "trace_comments": 1200000,
}

RELATIVE_FLOOR_PCT = 98

# csv's default 128KB field cap is smaller than some TRACE comment blobs.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def _count_reader(fh):
    """Data rows in an open text handle, header excluded.

    csv.reader, not a line count: TRACE comments and RMP review text contain
    newlines inside quoted fields, so counting lines over-reports and would let
    a truncated file clear its floor.
    """
    reader = csv.reader(fh)
    try:
        next(reader)
    except StopIteration:
        return 0
    return sum(1 for row in reader if row)


def count_rows(path):
    """Data-row count for a .csv or a .zip holding one."""
    path = str(path)
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            members = [n for n in z.namelist() if n.endswith(".csv")]
            if not members:
                raise ValueError(f"{path} contains no .csv member")
            with z.open(members[0]) as raw:
                return _count_reader(
                    line.decode("utf-8", "replace") for line in raw
                )
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        return _count_reader(fh)


def resolve_path(data_dir, name):
    """The path holding `name` in `data_dir`, or None when absent."""
    for candidate in FILES[name]:
        path = os.path.join(str(data_dir), candidate)
        if os.path.exists(path):
            return _Path(path)
    return None


class _Path(str):
    """A str that also answers .name, so callers can read either form."""

    @property
    def name(self):
        return os.path.basename(self)


def collect_counts(data_dir):
    """Row count per logical name; None for a file that is not there."""
    counts = {}
    for name in FILES:
        path = resolve_path(data_dir, name)
        counts[name] = None if path is None else count_rows(path)
    return counts


def check(counts, baseline=None, accept_lower=False):
    """Every floor violation in `counts`, as human-readable strings.

    An empty list means the scrape is safe to write. Reports all problems
    rather than the first, so one run surfaces everything that is wrong.
    """
    problems = []
    baseline = baseline or {}
    for name in FILES:
        count = counts.get(name)
        if count is None:
            problems.append(
                f"Missing required data file: {name}. "
                "precompute.py reads all six; aborting before DB writes."
            )
            continue

        floor = ABSOLUTE_FLOORS[name]
        if count < floor:
            problems.append(
                f"{name}: only {count} rows (< {floor} absolute floor). "
                "Aborting to protect existing data."
            )
            continue

        if accept_lower:
            continue

        # A baseline of 0 (or absent) means there was nothing to compare
        # against — first run, or a rebuilt store. Fall back to the absolute
        # floor rather than blocking forever.
        previous = baseline.get(name) or 0
        if previous <= 0:
            continue

        relative_floor = previous * RELATIVE_FLOOR_PCT // 100
        if count < relative_floor:
            problems.append(
                f"{name}: {count} rows < {RELATIVE_FLOOR_PCT}% of last week's "
                f"{previous} (floor {relative_floor}). Aborting; re-run with "
                "accept_lower_counts if the drop is real."
            )
    return problems


def _cmd_baseline(args):
    counts = collect_counts(args.data_dir)
    present = {k: v for k, v in counts.items() if v is not None}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(present, fh, indent=2, sort_keys=True)
    for name in FILES:
        print(f"  {name}: {counts[name] if counts[name] is not None else 'absent'}")
    print(f"Baseline written to {args.out}")
    return 0


def _cmd_check(args):
    baseline = None
    if args.baseline and os.path.exists(args.baseline):
        with open(args.baseline, "r", encoding="utf-8") as fh:
            baseline = json.load(fh)
    elif args.baseline:
        print(f"No baseline at {args.baseline} — absolute floors only.")

    counts = collect_counts(args.data_dir)
    for name in FILES:
        current = counts[name]
        was = (baseline or {}).get(name)
        print(f"  {name}: {current if current is not None else 'absent'}"
              + (f" (previous: {was})" if was else ""))

    problems = check(counts, baseline, accept_lower=args.accept_lower)
    if args.accept_lower:
        print("accept_lower_counts set — skipping relative floors.")
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    print("All floors passed.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Data-refresh scrape guardrails")
    sub = parser.add_subparsers(dest="command", required=True)

    p_baseline = sub.add_parser("baseline", help="Snapshot counts before a scrape")
    p_baseline.add_argument("--data-dir", required=True)
    p_baseline.add_argument("--out", required=True)
    p_baseline.set_defaults(func=_cmd_baseline)

    p_check = sub.add_parser("check", help="Enforce floors after a scrape")
    p_check.add_argument("--data-dir", required=True)
    p_check.add_argument("--baseline")
    p_check.add_argument("--accept-lower", action="store_true")
    p_check.set_defaults(func=_cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
