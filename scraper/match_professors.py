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
    dept_norm: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self.dept_norm = normalize_name(self.department) if self.department else ""

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

        # PERFORMANCE: fuzzy matching blocks on first letter. process.extract
        # over all ~6.6k surnames per token is ~8ms; bucketing by initial cuts
        # the corpus ~26x (~250 avg) and the full run from hours to minutes.
        # Typos rarely change a surname's leading letter; the ones that do
        # (homophones like C/K) are caught by the phonetic layer instead.
        self.corpus_by_initial: Dict[str, List[str]] = defaultdict(list)
        for s in self.last_name_corpus:
            if s:
                self.corpus_by_initial[s[0]].append(s)


# Permissive on purpose. NEU has real 20xx-series courses (CS2000, DS2000, ...),
# so we must NOT exclude 4-digit "years" here — doing so drops 38 real codes.
# When this runs over free-form Reddit text (Task 6), a phantom match like
# "FALL2024" is harmless: it just won't exist in the course_map, so the
# course_map.get(code, set()) lookup returns empty and contributes nothing.
_COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,4})[\s-]?(\d{4})\b")


def parse_course_code(display_name: str) -> Optional[str]:
    """Extract the course code (e.g. 'ENGW3302') from a TRACE displayName."""
    m = _COURSE_CODE_RE.search(display_name or "")
    return f"{m.group(1)}{m.group(2)}" if m else None


def load_course_map(trace_csv: str, index: "ProfessorIndex") -> Dict[str, Set[str]]:
    """Map course_code -> set of instructor name_keys that exist in the catalog.

    Only instructors whose normalized name resolves to a catalog professor are
    kept, so course context always lands on a real slug.
    """
    catalog_keys = set(index.by_full_name.keys())
    course_map: Dict[str, Set[str]] = defaultdict(set)
    if not os.path.exists(trace_csv):
        return course_map
    with open(trace_csv, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = parse_course_code(row.get("displayName", ""))
            if not code:
                continue
            full = f"{row.get('instructorFirstName','')} {row.get('instructorLastName','')}"
            nk = normalize_name(full)
            if nk in catalog_keys:
                course_map[code].add(nk)
    return course_map


@dataclass
class Candidate:
    name_key: str
    confidence: float
    method: str
    matched_token: str


_CAP_RUN_RE = re.compile(r"\b([A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+)*)\b")


def _ascii_fold_keep_case(text: str) -> str:
    """NFKD ASCII-fold and straighten curly quotes WITHOUT lowercasing.

    The cap-run regex needs capitalization intact, but Reddit/iOS text uses the
    curly apostrophe U+2019 ("O'Brien") which the regex's straight-quote class
    misses — silently dropping the 54 apostrophe-surname professors in the
    catalog. Fold to straight ASCII first, preserving case for the regex.
    """
    s = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.replace("‘", "'").replace("’", "'")


def extract_tokens(text: str) -> Tuple[str, List[str]]:
    """Return (normalized_text, capitalized_tokens).

    - normalized_text: lowercased/folded, for exact full-name + dept/firstname
      word-boundary checks.
    - capitalized_tokens: each capitalized run, normalized (last/fuzzy/phonetic).
    """
    text = text or ""
    norm_text = normalize_name(text)
    folded = _ascii_fold_keep_case(text)
    cap_tokens = [normalize_name(m.group(1)) for m in _CAP_RUN_RE.finditer(folded)]
    cap_tokens = [t for t in cap_tokens if t]
    return norm_text, cap_tokens


def _contains_word(norm_text: str, phrase: str) -> bool:
    """Word-boundary containment, so short tokens like dept 'is' don't match
    inside 'this'/'discuss'. `phrase` must already be normalized."""
    return bool(phrase) and bool(
        re.search(r"\b" + re.escape(phrase) + r"\b", norm_text)
    )


def match_item(
    text: str, index: ProfessorIndex, course_map: Dict[str, Set[str]]
) -> List[Candidate]:
    """Run Layers 1-5 over one item's text; return scored candidates.

    A single professor may be emitted by more than one layer (e.g. exact_full
    AND lastname for a full-name mention). Callers must dedupe by name_key,
    keeping the max confidence — `aggregate` does this.
    """
    norm_text, cap_tokens = extract_tokens(text)
    cands: List[Candidate] = []

    # Course codes present in this item (used by L2 boost and L5).
    codes_present = {f"{m.group(1)}{m.group(2)}"
                     for m in _COURSE_CODE_RE.finditer(text.upper())}
    course_keys_present: Set[str] = set()
    for code in codes_present:
        course_keys_present |= course_map.get(code, set())

    # Layer 1: exact full name (word-boundary substring on normalized text).
    # PERFORMANCE: never iterate all ~9k professors per item — over 500k items
    # that is billions of regex calls. Only consider professors whose LAST name
    # appears as a token in this item (candidate set is tiny), then confirm the
    # full normalized name is present. Tradeoff: a fully-lowercased full name
    # ("i had anatoliy kuznetsov") whose last name was never capitalized is not
    # gated in — acceptable, since requiring capitalization cuts false positives
    # and such mentions are rare.
    last_tokens = {t.split()[-1] for t in cap_tokens if t}
    full_checked: Set[str] = set()
    for last in last_tokens:
        for prof in index.by_last_name.get(last, []):
            nk = prof.name_key
            if nk in full_checked:
                continue
            full_checked.add(nk)
            if re.search(r"\b" + re.escape(nk) + r"\b", norm_text):
                cands.append(Candidate(nk, 1.00, "exact_full", nk))

    # Layer 2: last name + disambiguation.
    for last in last_tokens:
        profs = index.by_last_name.get(last, [])
        if not profs:
            continue
        for prof in profs:
            conf = 0.85
            # First-name / dept checks use word boundaries so short tokens
            # ("is", "cs") don't match inside unrelated words. dept_norm is
            # precomputed on the Professor to avoid re-normalizing in this hot
            # loop (~9M calls over a full run).
            if prof.first_name and _contains_word(norm_text, prof.first_name):
                conf = 0.97
            if _contains_word(norm_text, prof.dept_norm):
                conf = min(1.0, conf + 0.05)
            if prof.name_key in course_keys_present:
                conf = max(conf, 0.98)
            cands.append(Candidate(prof.name_key, conf, "lastname", last))

    # Layer 3: fuzzy on capitalized tokens vs. same-initial surname bucket.
    # Note: the 0.75 ceiling is intentionally below the default resolve
    # threshold (0.80), so a fuzzy hit alone never auto-resolves — it needs
    # corroboration or lands as `ambiguous`. Revisit during calibration (Task 11).
    # Gates (all required for the full run to finish in minutes, not hours):
    #   - len(tok_last) >= 4: 3-char tokens fuzzy-match too much noise.
    #   - skip exact surnames: those are Layer 2's job.
    #   - only the same-initial bucket: see ProfessorIndex.corpus_by_initial.
    for token in cap_tokens:
        tok_last = token.split()[-1]
        if len(tok_last) < 4 or tok_last in index.by_last_name:
            continue
        bucket = index.corpus_by_initial.get(tok_last[0])
        if not bucket:
            continue
        results = process.extract(
            tok_last, bucket, scorer=fuzz.WRatio, score_cutoff=85, limit=3,
        )
        for match_last, score, _ in results:
            profs = index.by_last_name.get(match_last, [])
            if len(profs) != 1:
                continue  # multi-hit fuzzy is too risky; skip
            cands.append(Candidate(profs[0].name_key, (score / 100) * 0.75,
                                   "fuzzy", token))

    # Layer 4: phonetic (metaphone) on capitalized tokens.
    for token in cap_tokens:
        tok_last = token.split()[-1]
        if tok_last in index.by_last_name:
            continue  # exact spelling already handled by Layer 2
        mp = jellyfish.metaphone(tok_last)
        if not mp:
            continue
        profs = index.by_metaphone.get(mp, [])
        if len(profs) != 1:
            continue
        if profs[0].last_name == tok_last:
            continue  # exact spelling already handled
        cands.append(Candidate(profs[0].name_key, 0.65, "phonetic", token))

    # Layer 5: course-code standalone.
    for code in codes_present:
        for nk in course_map.get(code, set()):
            cands.append(Candidate(nk, 0.70, "course_context", code))

    return cands


@dataclass
class MatchResult:
    name_key: str                 # winning prof (resolved) or "" (ambiguous)
    confidence: float
    method: str
    matched_token: str
    status: str                   # "resolved" | "ambiguous"
    candidate_keys: List[str] = field(default_factory=list)


def aggregate(
    candidates: List[Candidate], resolve_threshold: float, margin: float, floor: float
) -> Optional[MatchResult]:
    """Collapse candidates to one MatchResult, or None if nothing clears `floor`.

    Keeps the max confidence per name_key, then decides:
    - top >= resolve_threshold AND next-best more than `margin` below -> resolved
    - else if anything >= floor -> ambiguous (all keys at/above floor)
    - else -> None
    """
    if not candidates:
        return None
    best: Dict[str, Candidate] = {}
    for c in candidates:
        cur = best.get(c.name_key)
        if cur is None or c.confidence > cur.confidence:
            best[c.name_key] = c
    ranked = sorted(best.values(), key=lambda c: c.confidence, reverse=True)
    top = ranked[0]
    if top.confidence < floor:
        return None
    # No second candidate -> gap is effectively infinite, so a lone candidate
    # above the resolve threshold always resolves.
    second = ranked[1].confidence if len(ranked) > 1 else 0.0
    if top.confidence >= resolve_threshold and (top.confidence - second) > margin:
        return MatchResult(
            name_key=top.name_key, confidence=top.confidence, method=top.method,
            matched_token=top.matched_token, status="resolved",
        )
    above_floor = [c for c in ranked if c.confidence >= floor]
    return MatchResult(
        name_key="", confidence=top.confidence, method=top.method,
        matched_token=top.matched_token, status="ambiguous",
        candidate_keys=[c.name_key for c in above_floor],
    )


def strip_fullname_prefix(fullname: str) -> str:
    """Strip a reddit fullname prefix (t1_, t3_, ...) returning the bare id."""
    return fullname.split("_", 1)[1] if "_" in (fullname or "") else (fullname or "")


# Title is case-insensitive (Prof/PROF/prof/Dr.), but the initial stays strictly
# [A-Z]: a real "Prof K" abbreviation capitalizes the initial, and keeping it
# strict avoids matching lowercase noise like "prof k" mid-sentence.
_PROF_INITIAL_RE = re.compile(r"\b(?i:prof|dr|professor|doctor)\.?\s+([A-Z])\b")
_PRONOUN_RE = re.compile(
    r"\b(he|she|they|him|her|them|the professor|the prof|this guy|this woman|"
    r"the legend|the goat)\b", re.IGNORECASE)


def has_context_trigger(text: str) -> bool:
    """True if the text has a 'Prof X' initial or a bare pronoun/honorific."""
    return bool(_PROF_INITIAL_RE.search(text or "") or _PRONOUN_RE.search(text or ""))


CONV_CONTEXT_CONFIDENCE = 0.55  # base for context-only matches; review in calibration


def resolve_conv_context(
    text: str, thread_subjects: Set[str], index: ProfessorIndex, floor: float,
) -> Optional[MatchResult]:
    """Resolve a context-triggered item against the thread's resolved subjects.

    Confidence is the fixed CONV_CONTEXT_CONFIDENCE (a single-signal layer, so
    resolve_threshold/margin don't apply); the result is dropped if it falls
    below `floor`, so raising the floor in calibration disables this layer.

    - exactly one subject -> resolved (for 'Prof X', the initial must match that
      subject's last-name initial, else drop).
    - multiple subjects -> ambiguous with all of them.
    - zero subjects (or below floor) -> None.
    """
    if not has_context_trigger(text) or not thread_subjects:
        return None
    if CONV_CONTEXT_CONFIDENCE < floor:
        return None
    subjects = sorted(thread_subjects)
    if len(subjects) == 1:
        nk = subjects[0]
        m = _PROF_INITIAL_RE.search(text or "")
        if m:
            prof = index.by_full_name.get(nk)
            initial = m.group(1).lower()
            if not prof or not prof.last_name.startswith(initial):
                return None
        return MatchResult(name_key=nk, confidence=CONV_CONTEXT_CONFIDENCE,
                           method="conv_context", matched_token="", status="resolved")
    return MatchResult(name_key="", confidence=CONV_CONTEXT_CONFIDENCE,
                       method="conv_context", matched_token="", status="ambiguous",
                       candidate_keys=subjects)


def read_csv_rows(path: str, limit: Optional[int] = None) -> Iterator[Dict[str, str]]:
    """Yield rows from a reddit CSV with the large-field-safe parser."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if limit is not None and i >= limit:
                return
            yield row


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

    course_map = {"CS3500": {"anatoliy kuznetsov"}}

    cands = match_item("Professor Anatoliy Kuznetsov is great", idx, course_map)
    check("L1 exact full", any(c.method == "exact_full" and c.confidence == 1.0 for c in cands))

    cands = match_item("Kuznetsov is brutal", idx, course_map)
    check("L2 single lastname", any(c.method == "lastname" and c.name_key == "anatoliy kuznetsov" for c in cands))

    cands = match_item("Kim is a great teacher", idx, course_map)
    check("L2 collision -> 2 candidates", len({c.name_key for c in cands if c.method == "lastname"}) == 2)

    cands = match_item("David Kim was fair", idx, course_map)
    check("L2 firstname disambiguates", any(c.name_key == "david kim" and c.confidence >= 0.97 for c in cands))

    cands = match_item("the CS3500 prof was tough", idx, course_map)
    check("L5 course context", any(c.method == "course_context" and c.name_key == "anatoliy kuznetsov" for c in cands))

    cands = match_item("Kuzentsov graded hard", idx, course_map)
    check("L3 fuzzy typo", any(c.method == "fuzzy" and c.name_key == "anatoliy kuznetsov" for c in cands))

    cands = match_item("Cuznetzof was strict", idx, course_map)
    check("L4 phonetic", any(c.method == "phonetic" for c in cands))

    code = parse_course_code("ENGW3302:09 (Advanced Writing in Tech Prof) - Laurie Nardone")
    check("course code parsed", code == "ENGW3302")
    check("course code none when absent", parse_course_code("random text") is None)
    check("course code with space", parse_course_code("ENGW 3302 syllabus") == "ENGW3302")
    check("course code rejects 3 digits", parse_course_code("CS 330") is None)
    check("course code keeps 20xx", parse_course_code("CS2000 intro") == "CS2000")

    def agg(cs):
        return aggregate(cs, resolve_threshold=0.80, margin=0.10, floor=0.55)

    r = agg([Candidate("anatoliy kuznetsov", 1.0, "exact_full", "x")])
    check("agg resolved single", r is not None and r.status == "resolved"
          and r.name_key == "anatoliy kuznetsov")

    r = agg([Candidate("jane kim", 0.85, "lastname", "kim"),
             Candidate("david kim", 0.85, "lastname", "kim")])
    check("agg ambiguous within margin", r is not None and r.status == "ambiguous"
          and set(r.candidate_keys) == {"jane kim", "david kim"})

    r = agg([Candidate("jane kim", 0.97, "lastname", "kim"),
             Candidate("david kim", 0.85, "lastname", "kim")])
    check("agg resolved beyond margin", r is not None and r.status == "resolved"
          and r.name_key == "jane kim")

    r = agg([Candidate("x", 0.40, "phonetic", "x")])
    check("agg below floor -> None", r is None)

    check("strip t3", strip_fullname_prefix("t3_9z0c3") == "9z0c3")
    check("strip t1", strip_fullname_prefix("t1_abc") == "abc")

    check("conv trigger prof initial", has_context_trigger("I think Prof K is fair"))
    check("conv trigger pronoun", has_context_trigger("honestly he is the worst"))
    check("conv no trigger", not has_context_trigger("the weather is nice today"))

    # Single-subject thread resolves a triggered item.
    subj = {"anatoliy kuznetsov"}
    r = resolve_conv_context("he's brutal", subj, idx, floor=0.55)
    check("conv single subject resolved", r is not None and r.status == "resolved"
          and r.name_key == "anatoliy kuznetsov" and r.method == "conv_context")

    # Prof-initial must match the subject's last initial.
    r = resolve_conv_context("Prof K rocks", {"anatoliy kuznetsov"}, idx, floor=0.55)
    check("conv prof-initial match", r is not None and r.status == "resolved")
    r = resolve_conv_context("Prof S rocks", {"anatoliy kuznetsov"}, idx, floor=0.55)
    check("conv prof-initial mismatch dropped", r is None)

    # Two subjects -> ambiguous.
    r = resolve_conv_context("he was great", {"jane kim", "david kim"}, idx, floor=0.55)
    check("conv two subjects ambiguous", r is not None and r.status == "ambiguous")

    # Floor above the conv-context confidence disables the layer.
    r = resolve_conv_context("he's brutal", {"anatoliy kuznetsov"}, idx, floor=0.60)
    check("conv dropped when below floor", r is None)

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
