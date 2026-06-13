"""
Professor mention matcher for r/NEU Reddit data.

Links Reddit posts and comments to professors in the RateMyHusky catalog.
Precision-first: mentions that cannot be pinned to one professor are recorded
as `ambiguous`, not guessed.

Usage
-----
    python match_professors.py                       # full run, default thresholds
    python match_professors.py --backup PATH.sql.gz  # override catalog backup
    python match_professors.py --no-conv-context     # skip thread-context pass
    python match_professors.py --limit 5000          # cap items (testing)
    python match_professors.py --calibrate           # wide-open run + sampling dump
    python match_professors.py --selftest            # offline checks, then exit
"""

__author__ = "RateMyHusky"
__version__ = "1.0.0"

import argparse
import csv
import gzip
import hashlib
import os
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

# Windows consoles default to cp1252 and can't encode the status glyphs below.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_BACKUP = os.path.join(
    REPO_ROOT, "backend", "backups", "ratemyhusky_new_20260602T001500Z.sql.gz"
)
TRACE_COURSES_CSV = os.path.join(
    REPO_ROOT, "backend", "Better_Scraper", "output_data", "trace_courses.csv"
)
REDDIT_DIR = os.path.join(SCRIPT_DIR, "reddit_data")
POSTS_CSV = os.path.join(REDDIT_DIR, "reddit_neu_posts.csv")
COMMENTS_CSV = os.path.join(REDDIT_DIR, "reddit_neu_comments.csv")
MENTIONS_CSV = os.path.join(REDDIT_DIR, "reddit_mentions.csv")
CALIBRATION_CSV = os.path.join(REDDIT_DIR, "calibration_sample.csv")

# CSV bodies can be very large; raise the field-size limit so the parser
# doesn't choke on long multi-line comment bodies.
csv.field_size_limit(10 * 1024 * 1024)


def normalize_name(name: Any) -> str:
    """Lowercase, NFKD ASCII-fold, collapse whitespace.

    Must match precompute.normalize_name so Reddit tokens key to the same
    canonical identity the catalog uses.
    """
    s = str(name).strip().lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def selftest() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures += 1

    check("normalize lowercases + folds", normalize_name("José  Ruiz") == "jose ruiz")
    check("normalize collapses ws", normalize_name("  a   b ") == "a b")

    print(f"\n  {'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return failures


def main() -> None:
    p = argparse.ArgumentParser(description="Match Reddit mentions to professors.")
    p.add_argument("--backup", default=DEFAULT_BACKUP)
    p.add_argument("--resolve-threshold", type=float, default=0.80)
    p.add_argument("--margin", type=float, default=0.10)
    p.add_argument("--floor", type=float, default=0.55)
    p.add_argument("--no-conv-context", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--calibrate", action="store_true")
    p.add_argument("--selftest", action="store_true", help="Run offline unit checks and exit")
    args = p.parse_args()

    if args.selftest:
        sys.exit(selftest())
    run(args)


def run(args: argparse.Namespace) -> None:
    raise NotImplementedError  # filled in Task 9


if __name__ == "__main__":
    main()
