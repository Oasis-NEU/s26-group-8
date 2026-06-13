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
        for p in professors:
            if p.name_key:
                # Catalog is deduplicated; name_key is unique per prof.
                self.by_full_name[p.name_key] = p
            if p.last_name:
                self.by_last_name[p.last_name].append(p)


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

# Surnames that are also common English words — a bare match on these is almost
# always not about a professor ("check the Law", "on the Hill", "you"). They only
# count when corroborated (first name / dept / course). Derived from the full
# 427k-comment corpus (surnames that frequently appear as common lowercase words)
# plus month names and observed noise; deliberately EXCLUDES legit frequent
# surnames like chen/wang/li/lee/kim/zhang/liu/smith (handled by
# collision->ambiguous instead).
_COMMON_WORD_SURNAMES = frozenset({
    "ai", "an", "august", "bath", "black", "board", "book", "can", "case",
    "charles", "crossing", "curry", "day", "dev", "don", "estabrook", "fan",
    "fine", "form", "forsyth", "francisco", "french", "green", "hall", "hand",
    "hands", "hastings", "he", "her", "high", "hill", "him", "hope", "house",
    "i", "ireland", "israel", "jesus", "kerr", "law", "list", "little",
    "london", "long", "love", "ma", "mac", "man", "march", "marino", "may",
    "min", "money", "oh", "pa", "page", "park", "place", "poor", "post",
    "power", "price", "re", "ready", "said", "small", "soon", "south",
    "spring", "staff", "summer", "ta", "to", "washington", "west", "white",
    "winter", "worth", "you",
})


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
    - capitalized_tokens: each capitalized run, normalized (for last-name match).
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
    """Run Layers 1-2 over one item's text; return scored candidates.

    A single professor may be emitted by more than one layer (e.g. exact_full
    AND lastname for a full-name mention). Callers must dedupe by name_key,
    keeping the max confidence — `aggregate` does this.
    """
    norm_text, cap_tokens = extract_tokens(text)
    cands: List[Candidate] = []

    # Course codes present in this item (used by L2 boost).
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
            # Corroboration. A single-character first name ("D Hope") is too weak
            # to count — it would match almost any text.
            has_first = (len(prof.first_name) >= 2
                         and _contains_word(norm_text, prof.first_name))
            has_dept = _contains_word(norm_text, prof.dept_norm)
            has_course = prof.name_key in course_keys_present

            # Bare last name (no corroboration) is inherently low-precision, so it
            # stays BELOW resolve_threshold (0.80) and can only become ambiguous.
            # Corroboration lifts it into the resolve range. Dept alone is weak,
            # so it nudges but does not by itself cross the threshold.
            conf = 0.70
            if has_dept:
                conf = 0.75
            if has_first:
                conf = 0.97
            if has_course:
                conf = max(conf, 0.98)

            # Stoplisted or very short surnames are pure noise when bare — drop
            # them from the output entirely (not even ambiguous).
            bare = not (has_first or has_dept or has_course)
            if bare and (last in _COMMON_WORD_SURNAMES or len(last) <= 2):
                continue
            cands.append(Candidate(prof.name_key, conf, "lastname", last))

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


MENTION_FIELDS = [
    "mention_id", "source_type", "source_id", "thread_id", "professor_slug",
    "professor_name", "name_key", "confidence", "method", "matched_token",
    "status", "candidate_slugs",
]


def mention_id(source_id: str, slug: str, token: str) -> str:
    return hashlib.sha1(f"{source_id}|{slug}|{token}".encode("utf-8")).hexdigest()[:16]


def _post_text(row: Dict[str, str]) -> str:
    return f"{row.get('title','')} {row.get('selftext','')}".strip()


def run(args: argparse.Namespace) -> None:
    print("→ loading catalog…")
    profs = load_catalog(args.backup)
    index = ProfessorIndex(profs)
    slug_by_key = {p.name_key: p.slug for p in profs}
    name_by_key = {p.name_key: p.name for p in profs}
    print(f"  {len(profs)} professors indexed")

    course_map = load_course_map(TRACE_COURSES_CSV, index)
    print(f"  {len(course_map)} course codes mapped")

    resolve_threshold = args.resolve_threshold
    margin = args.margin
    floor = args.floor

    # We only KEEP text for items that produced no confident match (candidates
    # for pass 2), to bound memory. Resolved subjects are tracked per thread.
    thread_subjects: Dict[str, Set[str]] = defaultdict(set)
    pending: List[Tuple[str, str, str, str]] = []  # for pass-2 reconsideration

    # try/finally (mirroring reddit_scrape.py) so a crash mid-run still flushes
    # and closes the file — a leaked write handle blocks re-runs on Windows.
    writer_fh = open(MENTIONS_CSV, "w", encoding="utf-8", newline="")
    writer = csv.DictWriter(writer_fh, fieldnames=MENTION_FIELDS)
    writer.writeheader()

    def emit(source_type: str, source_id: str, thread_id: str, mr: MatchResult) -> None:
        # Shared fields; the resolved/ambiguous branches only differ in the
        # prof identity columns. A shared base avoids silently dropping a column
        # in one branch if MENTION_FIELDS grows.
        if mr.status == "resolved":
            slug = slug_by_key.get(mr.name_key, "")
            key_for_id, prof_slug, prof_name, nk, cand = (
                slug, slug, name_by_key.get(mr.name_key, ""), mr.name_key, "")
        else:  # ambiguous
            cand = "|".join(slug_by_key.get(k, "") for k in mr.candidate_keys)
            key_for_id, prof_slug, prof_name, nk = cand, "", "", ""
        writer.writerow({
            "mention_id": mention_id(source_id, key_for_id, mr.matched_token),
            "source_type": source_type, "source_id": source_id,
            "thread_id": thread_id, "professor_slug": prof_slug,
            "professor_name": prof_name, "name_key": nk,
            "confidence": round(mr.confidence, 3), "method": mr.method,
            "matched_token": mr.matched_token, "status": mr.status,
            "candidate_slugs": cand,
        })

    def handle(source_type: str, source_id: str, thread_id: str, text: str) -> None:
        mr = aggregate(match_item(text, index, course_map),
                       resolve_threshold, margin, floor)
        if mr is None:
            if not args.no_conv_context and has_context_trigger(text):
                pending.append((source_type, source_id, thread_id, text))
            return
        emit(source_type, source_id, thread_id, mr)
        if mr.status == "resolved":
            thread_subjects[thread_id].add(mr.name_key)

    try:
        # ---- Pass 1: isolated resolution; collect rows + thread subjects ----
        print("→ pass 1: matching posts and comments…")
        n = 0
        for row in read_csv_rows(POSTS_CSV, args.limit):
            pid = row.get("id", "")
            handle("post", pid, pid, _post_text(row))
            n += 1
        for row in read_csv_rows(COMMENTS_CSV, args.limit):
            cid = row.get("id", "")
            tid = strip_fullname_prefix(row.get("link_id", ""))
            handle("comment", cid, tid, row.get("body", ""))
            n += 1
        print(f"  pass 1 done: {n} items, {len(pending)} pending context items")

        # ---- Pass 2: conversation context ----
        if not args.no_conv_context:
            print("→ pass 2: conversation context…")
            resolved2 = 0
            for source_type, source_id, thread_id, text in pending:
                subj = thread_subjects.get(thread_id)
                if not subj:
                    continue
                mr = resolve_conv_context(text, subj, index, floor)
                if mr is not None:
                    emit(source_type, source_id, thread_id, mr)
                    resolved2 += 1
            print(f"  pass 2 done: {resolved2} context mentions emitted")
    finally:
        writer_fh.close()
    print(f"\n  ✓ wrote mentions → {MENTIONS_CSV}")


_BANDS = [(0.55, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 0.95), (0.95, 1.001)]


def confidence_band(conf: float) -> str:
    for lo, hi in _BANDS:
        if lo <= conf < hi:
            top = "1.00" if hi > 1.0 else f"{hi:.2f}"
            return f"{lo:.2f}-{top}"
    return "below"


def calibrate(args: argparse.Namespace) -> None:
    """Wide-open run: emit a stratified sample (by band x method) for eyeballing.

    Captures every raw candidate (no floor/aggregate applied) so the full
    confidence range is observable. Writes calibration_sample.csv with up to N
    rows per (band, method) cell, including the surrounding text snippet so
    precision can be judged by hand.
    """
    import random
    random.seed(0)
    per_cell = 25

    profs = load_catalog(args.backup)
    index = ProfessorIndex(profs)
    name_by_key = {p.name_key: p.name for p in profs}
    course_map = load_course_map(TRACE_COURSES_CSV, index)

    buckets: Dict[Tuple[str, str], List[Dict[str, str]]] = defaultdict(list)
    seen: Dict[Tuple[str, str], int] = defaultdict(int)

    def consider(source_id: str, text: str, c: Candidate) -> None:
        cell = (confidence_band(c.confidence), c.method)
        seen[cell] += 1
        # Reservoir sampling so the sample is unbiased across the full stream.
        bucket = buckets[cell]
        if len(bucket) < per_cell:
            bucket.append(_calib_row(source_id, text, c, name_by_key))
        else:
            j = random.randint(0, seen[cell] - 1)
            if j < per_cell:
                bucket[j] = _calib_row(source_id, text, c, name_by_key)

    n = 0
    for row in read_csv_rows(POSTS_CSV, args.limit):
        pid = row.get("id", "")
        text = _post_text(row)
        for c in match_item(text, index, course_map):
            consider(pid, text, c)
        n += 1
    for row in read_csv_rows(COMMENTS_CSV, args.limit):
        cid = row.get("id", "")
        text = row.get("body", "")
        for c in match_item(text, index, course_map):
            consider(cid, text, c)
        n += 1

    with open(CALIBRATION_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "band", "method", "confidence", "source_id", "name_key",
            "professor_name", "matched_token", "snippet",
        ])
        w.writeheader()
        for cell in sorted(buckets):
            for r in buckets[cell]:
                w.writerow(r)

    print(f"  scanned {n} items")
    print("  candidate counts per (band, method):")
    for cell in sorted(seen):
        print(f"    {cell[0]:>11}  {cell[1]:<14} {seen[cell]}")
    print(f"\n  ✓ wrote sample → {CALIBRATION_CSV}")
    print("  Inspect it by hand: find the band where precision falls off — that")
    print("  is your --resolve-threshold. Record chosen values + evidence in the spec.")


def _calib_row(source_id, text, c, name_by_key):
    idx = (text or "").lower().find(c.matched_token.lower())
    start = max(0, idx - 40)
    snippet = " ".join((text or "")[start:idx + 60].split())
    return {
        "band": confidence_band(c.confidence), "method": c.method,
        "confidence": round(c.confidence, 3), "source_id": source_id,
        "name_key": c.name_key, "professor_name": name_by_key.get(c.name_key, ""),
        "matched_token": c.matched_token, "snippet": snippet,
    }


def golden_selftest(check) -> None:
    """End-to-end assertions on real catalog at the locked default thresholds."""
    if not os.path.exists(DEFAULT_BACKUP):
        check("golden set (skipped, no backup)", True)
        return
    profs = load_catalog(DEFAULT_BACKUP)
    index = ProfessorIndex(profs)
    course_map = load_course_map(TRACE_COURSES_CSV, index)
    def resolve(text):
        return aggregate(match_item(text, index, course_map), 0.80, 0.10, 0.55)

    # Pick a real prof with a distinctive (non-collision, non-stoplisted) surname.
    distinctive = next(
        p for p in profs
        if len(index.by_last_name[p.last_name]) == 1
        and p.last_name not in _COMMON_WORD_SURNAMES
        and len(p.last_name) >= 5 and " " in p.name_key
    )
    # full name -> resolved to that prof (title-case the name_key so cap-run regex
    # captures all parts; .name may have inconsistent casing in the catalog)
    display = distinctive.name_key.title()
    r = resolve(f"I really enjoyed Professor {display} this semester")
    check("golden: full name resolves",
          r is not None and r.status == "resolved" and r.name_key == distinctive.name_key)
    # bare distinctive surname -> ambiguous now (bare last names don't resolve)
    r = resolve(f"{distinctive.last_name.title()} was a fair grader")
    check("golden: bare surname does not resolve",
          r is None or r.status == "ambiguous")
    # but WITH the first name it resolves
    r = resolve(f"{distinctive.first_name.title()} {distinctive.last_name.title()} was fair")
    check("golden: corroborated surname resolves",
          r is not None and r.status == "resolved" and r.name_key == distinctive.name_key)
    # a real collision surname bare -> ambiguous
    collide = next(ln for ln, ps in index.by_last_name.items() if len(ps) >= 5 and ln not in _COMMON_WORD_SURNAMES)
    r = resolve(f"{collide.title()} is the worst professor honestly")
    check("golden: collision surname ambiguous",
          r is not None and r.status == "ambiguous")
    # pure noise -> no match
    r = resolve("the dining hall food is terrible and the gym is crowded")
    check("golden: noise sentence -> no resolved prof",
          r is None or r.status == "ambiguous")
    # a stoplisted common-word surname bare -> not resolved to a prof
    r = resolve("you should check the law before signing the lease")
    check("golden: common-word bare not resolved",
          r is None or r.status != "resolved")


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

    course_map = {"CS3500": {"anatoliy kuznetsov"}}

    cands = match_item("Professor Anatoliy Kuznetsov is great", idx, course_map)
    check("L1 exact full", any(c.method == "exact_full" and c.confidence == 1.0 for c in cands))

    cands = match_item("Kuznetsov is brutal", idx, course_map)
    check("L2 bare lastname is sub-threshold",
          any(c.method == "lastname" and c.name_key == "anatoliy kuznetsov"
              and c.confidence < 0.80 for c in cands))

    cands = match_item("Kim is a great teacher", idx, course_map)
    check("L2 collision -> 2 candidates", len({c.name_key for c in cands if c.method == "lastname"}) == 2)

    cands = match_item("David Kim was fair", idx, course_map)
    check("L2 firstname disambiguates", any(c.name_key == "david kim" and c.confidence >= 0.97 for c in cands))

    cands = match_item("Kuznetsov CS3500 was tough", idx, course_map)
    check("course code boosts lastname", any(c.method == "lastname" and c.name_key == "anatoliy kuznetsov" and c.confidence >= 0.98 for c in cands))

    stop_fix = [Professor("john-law", "John Law", "john law", "Finance", "Business", 5, 5, 1)]
    stop_idx = ProfessorIndex(stop_fix)
    cands = match_item("I think the Law is unfair", stop_idx, {})
    check("common-word surname bare dropped",
          not any(c.method == "lastname" for c in cands))
    cands = match_item("John Law was a great teacher", stop_idx, {})
    check("common-word surname with firstname kept",
          any(c.method == "lastname" and c.name_key == "john law" for c in cands))

    # Very short surname needs corroboration too.
    short_fix = [Professor("li-ta", "Li Ta", "li ta", "Music", "Arts", 4, 4, 1)]
    short_idx = ProfessorIndex(short_fix)
    cands = match_item("ha ta that was funny", short_idx, {})
    check("short surname bare dropped",
          not any(c.method == "lastname" for c in cands))
    cands = match_item("Li Ta is a great teacher", short_idx, {})
    check("short surname with firstname kept",
          any(c.method == "lastname" and c.name_key == "li ta" for c in cands))

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

    a = mention_id("c123", "jane-kim", "kim")
    b = mention_id("c123", "jane-kim", "kim")
    c = mention_id("c123", "david-kim", "kim")
    check("mention_id stable", a == b)
    check("mention_id distinct per prof", a != c)

    check("band low", confidence_band(0.60) == "0.55-0.65")
    check("band mid", confidence_band(0.81) == "0.75-0.85")
    check("band top", confidence_band(1.0) == "0.95-1.00")

    golden_selftest(check)

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
    if args.calibrate:
        calibrate(args)
        return
    run(args)


if __name__ == "__main__":
    main()
