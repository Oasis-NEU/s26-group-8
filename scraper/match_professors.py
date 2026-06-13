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

import jellyfish
from rapidfuzz import fuzz, process

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


def parse_sql_values(body: str) -> List[List[Optional[str]]]:
    """Parse the VALUES portion of an INSERT into a list of row value-lists.

    `body` is everything after `VALUES`, e.g. "(1,'a'),(2,'b')". Quoted strings
    are returned with surrounding quotes removed and '' un-escaped to '. The
    unquoted literal NULL becomes None. Unquoted numerics are returned as their
    raw (stripped) string; the caller coerces.
    """
    rows: List[List[Optional[str]]] = []
    i, n = 0, len(body)
    while i < n:
        if body[i] != "(":
            i += 1
            continue
        i += 1  # past '('
        row: List[Optional[str]] = []
        chars: List[str] = []
        in_str = False
        was_quoted = False
        while i < n:
            c = body[i]
            if in_str:
                if c == "'":
                    if i + 1 < n and body[i + 1] == "'":  # escaped ''
                        chars.append("'")
                        i += 2
                        continue
                    in_str = False
                    i += 1
                    continue
                chars.append(c)
                i += 1
                continue
            # not in string
            if c == "'":
                in_str = True
                was_quoted = True
                i += 1
                continue
            if c in (",", ")"):
                token = "".join(chars)
                if not was_quoted and token.strip() == "NULL":
                    row.append(None)
                else:
                    row.append(token if was_quoted else token.strip())
                chars = []
                was_quoted = False
                if c == ")":
                    i += 1
                    break
                i += 1
                continue
            chars.append(c)
            i += 1
        rows.append(row)
    return rows


@dataclass
class Professor:
    slug: str
    name: str
    name_key: str
    department: str
    college: str
    num_ratings: int
    total_reviews: int
    total_comments: int

    @property
    def first_name(self) -> str:
        parts = self.name_key.split()
        return parts[0] if parts else ""

    @property
    def last_name(self) -> str:
        parts = self.name_key.split()
        return parts[-1] if parts else ""


# Column order from CREATE TABLE professors_catalog in the backup.
_CATALOG_COLS = [
    "slug", "name", "name_key", "department", "college", "avg_rating",
    "rmp_rating", "trace_rating", "num_ratings", "trace_reviews",
    "total_reviews", "would_take_again_pct", "difficulty", "professor_url",
    "image_url", "avg_hours", "total_comments",
]


def _to_int(v: Optional[str]) -> int:
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def load_catalog(backup_path: str) -> List[Professor]:
    """Extract professors_catalog rows from the gzipped SQL backup.

    Reads every `INSERT INTO professors_catalog (...) VALUES ...;` statement and
    parses its rows. name_key is already alias-merged/canonical in this table,
    so no ALIAS_MAP is applied here.
    """
    idx = {c: i for i, c in enumerate(_CATALOG_COLS)}
    profs: List[Professor] = []
    skipped = 0
    with gzip.open(backup_path, "rt", encoding="utf-8", errors="replace") as f:
        buf: List[str] = []
        capturing = False
        for line in f:
            if not capturing and re.match(r"INSERT INTO professors_catalog[ (]", line):
                capturing = True
                buf = [line]
            elif capturing:
                buf.append(line)
            else:
                continue
            if capturing and line.rstrip().endswith(";"):
                stmt = "".join(buf)
                vpos = stmt.find(" VALUES")
                if vpos == -1:
                    capturing = False
                    buf = []
                    continue
                body = stmt[vpos + len(" VALUES"):].rstrip().rstrip(";")
                for row in parse_sql_values(body):
                    if len(row) < len(_CATALOG_COLS):
                        skipped += 1
                        continue
                    profs.append(Professor(
                        slug=row[idx["slug"]] or "",
                        name=row[idx["name"]] or "",
                        name_key=normalize_name(row[idx["name_key"]] or ""),
                        department=row[idx["department"]] or "",
                        college=row[idx["college"]] or "",
                        num_ratings=_to_int(row[idx["num_ratings"]]),
                        total_reviews=_to_int(row[idx["total_reviews"]]),
                        total_comments=_to_int(row[idx["total_comments"]]),
                    ))
                capturing = False
                buf = []
    if skipped:
        print(f"  ⚠ load_catalog skipped {skipped} short rows")
    return profs


class ProfessorIndex:
    """Lookup structures over the catalog for the matching layers."""

    def __init__(self, professors: List[Professor]) -> None:
        self.professors = professors  # kept for callers that iterate the raw list
        # keys are normalized name_keys (already normalize_name'd), e.g. "anatoliy kuznetsov"
        self.by_full_name: Dict[str, Professor] = {}
        self.by_last_name: Dict[str, List[Professor]] = defaultdict(list)
        self.by_metaphone: Dict[str, List[Professor]] = defaultdict(list)
        for p in professors:
            if p.name_key:
                # Catalog is deduplicated; name_key is unique per prof.
                self.by_full_name[p.name_key] = p
            if p.last_name:
                self.by_last_name[p.last_name].append(p)
                mp = jellyfish.metaphone(p.last_name)
                if mp:
                    self.by_metaphone[mp].append(p)
        # Unique last names form the fuzzy-match corpus.
        self.last_name_corpus: List[str] = sorted(self.by_last_name.keys())


def selftest() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures += 1

    check("normalize lowercases + folds", normalize_name("José  Ruiz") == "jose ruiz")
    check("normalize collapses ws", normalize_name("  a   b ") == "a b")

    rows = parse_sql_values("(1,'a','b'),(2,'O''Brien',NULL)")
    check("sql parse row count", len(rows) == 2)
    check("sql parse plain", rows[0] == ["1", "a", "b"])
    check("sql parse escaped quote", rows[1][1] == "O'Brien")
    check("sql parse NULL -> None", rows[1][2] is None)
    check("sql parse comma in string",
          parse_sql_values("('a, b','c')")[0] == ["a, b", "c"])
    check("sql parse paren in string",
          parse_sql_values("('f(x)','y')")[0] == ["f(x)", "y"])
    check("sql parse empty string", parse_sql_values("('')")[0] == [""])
    check("sql parse quoted NULL stays string", parse_sql_values("('NULL')")[0] == ["NULL"])

    if os.path.exists(DEFAULT_BACKUP):
        profs = load_catalog(DEFAULT_BACKUP)
        check("catalog loads many profs", len(profs) > 5000)
        check("catalog has name_key", all(p.name_key for p in profs[:50]))
        sample = next((p for p in profs if " " in p.name_key), None)
        check("derives last name", sample is not None and sample.last_name != "")
    else:
        check("catalog backup present (skipped)", True)

    fixtures = [
        Professor("anatoliy-kuznetsov", "Anatoliy Kuznetsov", "anatoliy kuznetsov",
                  "Computer Science", "Khoury", 40, 40, 10),
        Professor("jane-kim", "Jane Kim", "jane kim", "Biology", "COS", 5, 5, 1),
        Professor("david-kim", "David Kim", "david kim", "Physics", "COS", 8, 8, 2),
    ]
    idx = ProfessorIndex(fixtures)
    check("full name index", idx.by_full_name.get("anatoliy kuznetsov") is not None)
    check("last name single", len(idx.by_last_name.get("kuznetsov", [])) == 1)
    check("last name collision", len(idx.by_last_name.get("kim", [])) == 2)
    check("metaphone buckets collisions",
          len(idx.by_metaphone.get(jellyfish.metaphone("kim"), [])) == 2)
    check("fuzzy corpus has lastnames", "kuznetsov" in idx.last_name_corpus)

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
