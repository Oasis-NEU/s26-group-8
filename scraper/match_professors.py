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


# Generational/credential suffixes that are never a surname. Stripped from the
# end of a name_key so the real surname indexes the professor.
_NAME_SUFFIXES = frozenset({
    "jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v",
})


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
        # A generational suffix ("jr"/"iii"/…) is not a surname: it would index
        # the prof under the suffix, so any bare "Jr."/"III" in Reddit text
        # ("Martin Luther King Jr.") would falsely match. Skip trailing suffix
        # tokens (and a dangling comma) to land on the real surname.
        parts = [p.strip(",") for p in self.name_key.split()]
        parts = [p for p in parts if p]
        while len(parts) > 1 and parts[-1] in _NAME_SUFFIXES:
            parts.pop()
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
    before = len(profs)
    profs = merge_suffix_duplicates(profs)
    if len(profs) != before:
        print(f"  merged {before - len(profs)} suffix-duplicate professor(s)")
    return profs


def _suffix_stripped_key(name_key: str) -> str:
    """name_key with any trailing generational-suffix tokens removed.

    "richard melloni jr" / "martin schwarz, jr." -> "richard melloni" /
    "martin schwarz". Used to detect catalog entries that are the same person
    listed with and without the suffix.
    """
    parts = [p.strip(",") for p in name_key.split()]
    parts = [p for p in parts if p]
    while len(parts) > 1 and parts[-1] in _NAME_SUFFIXES:
        parts.pop()
    return " ".join(parts)


def merge_suffix_duplicates(professors: List[Professor]) -> List[Professor]:
    """Collapse same-person suffix variants to one canonical professor.

    Two entries whose name_key is identical after stripping a trailing
    generational suffix ("richard melloni" vs "richard melloni jr") are the same
    person split across two catalog rows. Keep the richer entry (most ratings,
    then most reviews) so the mention count isn't split and a single mention
    can't emit both. Entries that merely share a surname are untouched — the key
    includes the FULL stripped name, so distinct first names never merge.
    """
    groups: Dict[str, List[Professor]] = defaultdict(list)
    order: List[str] = []
    for p in professors:
        k = _suffix_stripped_key(p.name_key)
        if k not in groups:
            order.append(k)
        groups[k].append(p)
    merged: List[Professor] = []
    for k in order:
        grp = groups[k]
        if len(grp) == 1:
            merged.append(grp[0])
            continue
        canonical = max(grp, key=lambda p: (p.num_ratings, p.total_reviews))
        merged.append(canonical)
    return merged


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
    # True when this candidate was only found via the lowercase path (the surname
    # never appeared capitalized). Such matches resolve themselves fine, but they
    # are too weak to AUTHORIZE the thread-surname anchor cascade — a lowercase
    # "jackson katz" / "salerno or katz" should not let every bare "katz" in the
    # thread resolve to a Katz professor.
    lc_origin: bool = False


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
    "i", "ireland", "israel", "jesus", "kerr", "king", "law", "list", "little",
    "london", "long", "love", "ma", "mac", "man", "march", "marino", "may",
    "min", "money", "oh", "pa", "page", "park", "place", "poor", "post",
    "power", "price", "re", "ready", "said", "small", "soon", "south",
    "spring", "staff", "stewart", "summer", "ta", "to", "washington", "west",
    "white", "willis", "winter", "worth", "you",
})

# Curated non-prof phrases that collide with common-word surnames (lowercased,
# substring-matched on normalized text).
_NONPROF_PHRASES = (
    "burger king", "king husky", "isabella stewart gardner", "taco bell",
    "tj max", "tj maxx", "law library", "ell hall", "dodge hall", "ryder hall",
    "shillman hall", "richards hall", "snell library", "marino center",
)
# Words signaling a building/place/brand/decision context rather than a person.
_NONPROF_CONTEXT = (
    "library", "gym", "volleyball", "court", "center", "museum", "statue",
    "negotiable", "worth it", "burger", "chief", "nupd",
)
# Signals the token really IS a professor — suppression must NOT fire.
_PROF_SIGNAL_RE = re.compile(
    r"\b(?i:prof|professor|dr|doctor|lecturer|teaches?|class with|took (?:him|her)|"
    r"(?:his|her|their) class|grade[ds]?|exam|midterm|syllabus|lecture)\b")
_COURSE_NEAR_RE = re.compile(r"\b[A-Za-z]{2,4}\s?\d{3,4}\b")


def _suppress_common_word(last: str, text: str) -> bool:
    """True if a common-word `last` should be dropped as a non-prof false positive.

    Drops only on a clear non-prof signal AND absence of a prof signal. Anything
    ambiguous is kept (returns False) to preserve recall.
    """
    if last not in _COMMON_WORD_SURNAMES:
        return False
    low = " ".join((text or "").lower().split())
    if _PROF_SIGNAL_RE.search(text or "") or _COURSE_NEAR_RE.search(text or ""):
        return False
    if any(p in low for p in _NONPROF_PHRASES):
        return True
    if any(re.search(r"\b" + re.escape(w) + r"\b", low) for w in _NONPROF_CONTEXT):
        return True
    return False


# Reporter abbreviations / citation markers signaling a legal case, not a person.
_CITATION_RE = re.compile(
    r"\b(?:v\.?\s|vs\.?\s)|"                       # "Fang v. ICE"
    r"\b(?:F\.\s?\d?d|F\.\s?Supp|U\.S\.|S\.\s?Ct|"  # reporters: F.3d, U.S., S.Ct
    r"Cir\.|F\.\s?App'x)\b")
# Book/author context: ISBN, edition, copyright, "Author:" — the surname is a
# textbook author on a for-sale / reading-list line, not a teacher.
_AUTHOR_RE = re.compile(
    r"\b(?:isbn|isbn-1[03]|author|edition|publisher|mcgraw|wiley|pearson|"
    r"cengage|routledge)\b|©|\(c\)\s?\d{4}", re.IGNORECASE)
# A building/venue: the surname is immediately followed by a place noun.
_BUILDING_AFTER_RE = re.compile(
    r"\b(?:arena|hall|library|institute|building|stadium|gym|gymnasium|"
    r"quad|fieldhouse|center|centre)\b", re.IGNORECASE)


def _suppress_nonperson(last: str, text: str) -> bool:
    """True if `last` lands on a clear non-person context: a textbook author
    line (ISBN/edition/©), a campus building (``Matthews Arena``), or a legal
    citation (``Fang v. ICE``). Applies to ANY surname (not just common words),
    but never fires when an explicit professor signal (prof/Dr/"his class"/
    teaches) is present, so real mentions are preserved. A bare nearby course
    code does NOT spare it — reading-list headers carry course codes too.
    """
    if not last:
        return False
    # An explicit person signal (prof/Dr/"his class"/teaches) always wins — these
    # are unambiguous. A bare nearby COURSE CODE does NOT, because a reading-list
    # header ("CHEM 1161: Gilbert, Kirss … 4th edition") carries a course code yet
    # is still an author line; the author/citation/building markers below are more
    # specific and override it.
    if _PROF_SIGNAL_RE.search(text or ""):
        return False
    # Fold curly quotes and drop apostrophes so a possessive/typo'd building name
    # ("Matthew's Arena") collapses onto the surname token ("matthews arena").
    low = _ascii_fold_keep_case(text or "").lower().replace("'", "")
    low = " ".join(low.split())
    if last not in low:
        return False
    # Building: the surname is directly followed by a venue noun ("matthews arena").
    if re.search(r"\b" + re.escape(last) + r"\s+(?:" + _BUILDING_AFTER_RE.pattern
                 + r")", low):
        return True
    # Legal citation anywhere in a short-ish span around the surname.
    if _CITATION_RE.search(text or ""):
        return True
    # Textbook author: ISBN/edition/©/Author marker present in the text.
    if _AUTHOR_RE.search(text or ""):
        return True
    return False


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


def _first_name_near(norm_text: str, first: str, last: str, window: int = 2) -> bool:
    """True if `first` appears within `window` tokens BEFORE `last` in norm_text.

    Real mentions write the name together ("john rachlin", or "thomas john
    plahovinsak" with a middle name), so the first name sits just before the
    surname. A first name scattered elsewhere ("George Floyd … Freddie Gray")
    is a coincidence and must not corroborate. The surname must follow the first
    name (names are written first-then-last), and the gap allows up to
    `window - 1` middle tokens. Both args must already be normalized.
    """
    if not first or not last:
        return False
    toks = norm_text.split()
    last_pos = [i for i, t in enumerate(toks) if t.strip(",") == last]
    first_pos = [i for i, t in enumerate(toks) if t.strip(",") == first]
    if not last_pos or not first_pos:
        return False
    for lp in last_pos:
        if any(0 < lp - fp <= window for fp in first_pos):
            return True
    return False


def _strip_possessive(token: str) -> str:
    """Drop a trailing English possessive so a surname gates in normally.

    "felleisen's" / "dupree's" -> the bare surname; "students'" -> "students".
    Genuine apostrophe-surnames are unaffected: "o'brien"/"d'angelo" keep their
    apostrophe (it is not at the possessive position), so this never corrupts
    the 54 apostrophe-surname professors in the catalog.
    """
    if token.endswith("'s"):
        return token[:-2]
    if token.endswith("s'"):
        return token[:-1]
    return token


def match_item(
    text: str, index: ProfessorIndex, course_map: Dict[str, Set[str]],
    match_lowercase: bool = False,
) -> List[Candidate]:
    """Run Layers 1-2 over one item's text; return scored candidates.

    A single professor may be emitted by more than one layer (e.g. exact_full
    AND lastname for a full-name mention). Callers must dedupe by name_key,
    keeping the max confidence — `aggregate` does this.

    match_lowercase: when True, surnames written entirely lowercase ("john
    rachlin overrated") also seed the layers — recovering casual mentions the
    cap-token gate misses. Precision is preserved by only letting a lowercase
    surname into Layer 2 when it is CORROBORATED (first name / dept / course);
    a bare lowercase surname is never admitted, since lowercasing surnames
    otherwise floods on dictionary-word names ("hammer", "boss", "rice").
    """
    norm_text, cap_tokens = extract_tokens(text)
    cands: List[Candidate] = []

    # Course codes present in this item (used by L2 boost).
    codes_present = {f"{m.group(1)}{m.group(2)}"
                     for m in _COURSE_CODE_RE.finditer(text.upper())}
    course_keys_present: Set[str] = set()
    for code in codes_present:
        course_keys_present |= course_map.get(code, set())

    # Surnames that anchor the candidate set. Normally only capitalized runs;
    # with match_lowercase we add every lowercase word too, so a fully-lowercase
    # surname can seed Layers 1-2. extract_tokens already lowercased norm_text.
    # Strip a trailing possessive when gating surnames: "Felleisen's" should gate
    # in Felleisen (the bare-surname/full-name boundary search in norm_text already
    # matches before the apostrophe; only the candidate-set gate was missing it).
    cap_last = {_strip_possessive(t.split()[-1]) for t in cap_tokens if t}
    if match_lowercase:
        last_tokens = set(cap_last)
        last_tokens.update(
            s for w in norm_text.split()
            for s in (_strip_possessive(w),) if s in index.by_last_name
        )
    else:
        last_tokens = cap_last

    # Layer 1: exact full name (word-boundary substring on normalized text).
    # PERFORMANCE: never iterate all ~9k professors per item — over 500k items
    # that is billions of regex calls. Only consider professors whose LAST name
    # appears as a token in this item (candidate set is tiny), then confirm the
    # full normalized name is present. A full-name match ("john rachlin") is
    # high-precision regardless of case, so when match_lowercase is on it gates
    # in lowercase full names too.
    full_checked: Set[str] = set()
    for last in last_tokens:
        lc_only_last = match_lowercase and last not in cap_last
        for prof in index.by_last_name.get(last, []):
            nk = prof.name_key
            if nk in full_checked:
                continue
            full_checked.add(nk)
            # A full name reached only via the lowercase path whose first name is
            # a bare initial ("d hope", "m rich") or a common word ("or katz",
            # "an smith") is a fragment that collides with ordinary lowercase text
            # ("i'd hope", "salerno or katz"). Require a real first name (>=2 char
            # and not a stoplisted word) in that case. A capitalized "D Hope" /
            # "Or Katz" is unaffected — capitalization already filters the noise.
            if lc_only_last and (
                len(prof.first_name) < 2 or prof.first_name in _COMMON_WORD_SURNAMES
            ):
                continue
            if re.search(r"\b" + re.escape(nk) + r"\b", norm_text):
                # A contiguous full name on an author line / legal citation is
                # still not a teacher ("David Massey, 4th edition"; "Fang v. ICE").
                if _suppress_nonperson(last, text):
                    continue
                cands.append(Candidate(nk, 1.00, "exact_full", nk,
                                       lc_origin=lc_only_last))

    # Layer 2: last name + disambiguation.
    for last in last_tokens:
        profs = index.by_last_name.get(last, [])
        if not profs:
            continue
        # A surname seen only in lowercase (not in any capitalized run) is the
        # relaxed case: admit it ONLY with corroboration. Bare lowercase
        # surnames are dropped below.
        lc_only = match_lowercase and last not in cap_last
        # A UNIQUE distinctive surname (one catalog prof, not a common word, of
        # real length) can corroborate on a first name found ANYWHERE — there is
        # no other professor to mis-attribute to, so "Felleisen … matthias" is
        # safe. A COLLISION or common-word surname must use PROXIMITY, since a
        # stray first name there picks the wrong prof ("George Floyd … Freddie
        # Gray" -> george gray) or a non-person ("don't … Burger King").
        surname_is_unique = (len(profs) == 1 and last not in _COMMON_WORD_SURNAMES
                             and len(last) >= 4)
        for prof in profs:
            # A single-character first name ("D Hope") is too weak to count — it
            # would match almost any text.
            if len(prof.first_name) < 2:
                has_first = False
            elif surname_is_unique:
                has_first = _contains_word(norm_text, prof.first_name)
            else:
                has_first = _first_name_near(norm_text, prof.first_name, last)
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

            bare = not (has_first or has_dept or has_course)
            # Stoplisted or very short surnames are pure noise when bare — drop
            # them from the output entirely (not even ambiguous).
            if bare and (last in _COMMON_WORD_SURNAMES or len(last) <= 2):
                continue
            if lc_only:
                # A surname seen only lowercase needs corroboration to enter L2
                # (a bare lowercase word is the false-positive flood we avoid)…
                if bare:
                    continue
                # …and even corroborated, a lowercase COMMON WORD ("christian law",
                # "d hope") corroborates too easily on coincidence, so it stays out
                # of L2. Such professors are still recoverable via a lowercase FULL
                # name in Layer 1, which is high-precision regardless of case.
                if last in _COMMON_WORD_SURNAMES or len(last) <= 2:
                    continue
            if not has_first and _suppress_common_word(last, text):
                continue
            # Author/building/citation contexts are non-persons even when the
            # first name is adjacent ("Author: David B. Massey"), so this guard
            # runs regardless of has_first; its own prof-signal check spares
            # genuine mentions.
            if _suppress_nonperson(last, text):
                continue
            cands.append(Candidate(prof.name_key, conf, "lastname", last,
                                   lc_origin=lc_only))

    return cands


@dataclass
class MatchResult:
    name_key: str                 # winning prof (resolved) or "" (ambiguous)
    confidence: float
    method: str
    matched_token: str
    status: str                   # "resolved" | "ambiguous"
    candidate_keys: List[str] = field(default_factory=list)
    lc_origin: bool = False       # winning candidate came only via lowercase path


PROMOTION_POLICIES = ("full_only", "full_plus_corroborated", "any_resolved")


def _dedup_rank(candidates: List[Candidate]) -> List[Candidate]:
    """Keep the max-confidence Candidate per name_key, ranked high->low."""
    best: Dict[str, Candidate] = {}
    for c in candidates:
        cur = best.get(c.name_key)
        if cur is None or c.confidence > cur.confidence:
            best[c.name_key] = c
    return sorted(best.values(), key=lambda c: c.confidence, reverse=True)


def _is_promotable(cand: Candidate, policy: str, resolve_threshold: float) -> bool:
    """Whether a deduped candidate becomes its own resolved row under `policy`."""
    if policy == "full_only":
        return cand.method == "exact_full"
    if policy == "full_plus_corroborated":
        return cand.method == "exact_full" or (
            cand.method == "lastname" and cand.confidence >= 0.97)
    if policy == "any_resolved":
        return cand.confidence >= resolve_threshold
    raise ValueError(f"unknown promotion policy {policy!r}")


def _has_exact_full(candidates: List[Candidate]) -> bool:
    """True if any candidate is an explicit in-text full name (exact_full)."""
    return any(c.method == "exact_full" for c in candidates)


def select_emissions(
    candidates: List[Candidate], resolve_threshold: float, margin: float,
    floor: float, policy: str = "full_only",
) -> List[MatchResult]:
    """Decide which rows to emit for one item's candidates.

    - >=1 promotable candidate -> one resolved row per promotable (multi-name).
    - else top candidate clears resolve_threshold with margin -> single resolved row.
    - else >=1 candidate >= floor -> single ambiguous row.
    - else -> [].
    Dedupe-by-name_key (max confidence) matches aggregate().
    """
    if not candidates:
        return []
    ranked = _dedup_rank(candidates)
    if ranked[0].confidence < floor:
        return []

    promotable = [c for c in ranked
                  if c.confidence >= floor and _is_promotable(c, policy, resolve_threshold)]
    if promotable:
        return [MatchResult(name_key=c.name_key, confidence=c.confidence,
                            method=c.method, matched_token=c.matched_token,
                            status="resolved", lc_origin=c.lc_origin)
                for c in promotable]

    top = ranked[0]
    second = ranked[1].confidence if len(ranked) > 1 else 0.0
    if top.confidence >= resolve_threshold and (top.confidence - second) > margin:
        return [MatchResult(name_key=top.name_key, confidence=top.confidence,
                            method=top.method, matched_token=top.matched_token,
                            status="resolved", lc_origin=top.lc_origin)]

    above_floor = [c for c in ranked if c.confidence >= floor]
    return [MatchResult(name_key="", confidence=top.confidence, method=top.method,
                        matched_token=top.matched_token, status="ambiguous",
                        candidate_keys=[c.name_key for c in above_floor])]


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
    ranked = _dedup_rank(candidates)
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
            lc_origin=top.lc_origin,
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
    policy = getattr(args, "policy", "full_plus_corroborated")

    # We only KEEP text for items that produced no confident match (candidates
    # for pass 2), to bound memory. Resolved subjects are tracked per thread.
    thread_subjects: Dict[str, Set[str]] = defaultdict(set)
    thread_surnames: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
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
        cands = match_item(text, index, course_map, args.match_lowercase)
        # Seed thread surname anchors from capitalized full names only (a lowercase-only
        # full name is too weak to authorize the cascade).
        for c in cands:
            if c.method == "exact_full" and not c.lc_origin:
                thread_surnames[thread_id][c.name_key.split()[-1]].add(c.name_key)
        ems = select_emissions(cands, resolve_threshold, margin, floor, policy)
        if not ems:
            if not args.no_conv_context and (
                has_context_trigger(text) or has_anchorable_surname(text, index)
            ):
                pending.append((source_type, source_id, thread_id, text))
            return
        resolved = [e for e in ems if e.status == "resolved"]
        if not resolved:
            # Single ambiguous result -> defer to pass 2 (thread_anchor may disambiguate).
            if not args.no_conv_context:
                pending.append((source_type, source_id, thread_id, text))
                return
            emit(source_type, source_id, thread_id, ems[0])
            return
        # >=1 resolved emission: emit each; explicit names settle the item, so it is NOT
        # queued for pass 2 -> conv_context can never override an in-text name.
        for e in resolved:
            emit(source_type, source_id, thread_id, e)
            if not e.lc_origin:
                thread_subjects[thread_id].add(e.name_key)

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

        # ---- Pass 2: thread surname anchoring, then conversation context ----
        if not args.no_conv_context:
            print("→ pass 2: thread anchoring + conversation context…")
            anchored2 = 0
            ctx2 = 0
            for source_type, source_id, thread_id, text in pending:
                # Thread surname anchor first (a name anchor beats a pronoun).
                mr = resolve_thread_anchor(text, thread_surnames.get(thread_id, {}), floor)
                if mr is not None:
                    emit(source_type, source_id, thread_id, mr)
                    if mr.status == "resolved":
                        thread_subjects[thread_id].add(mr.name_key)
                    anchored2 += 1
                    continue
                # Then conversation context (pronoun / "Prof X" against subjects) — but
                # NEVER override an explicit in-text full name. If match_item finds an
                # exact_full here, fall through to the aggregate result instead of
                # inventing a thread guess (guards the adam-ding -> marco-rainho bug).
                cands2 = match_item(text, index, course_map)
                subj = thread_subjects.get(thread_id)
                if subj and has_context_trigger(text) and not _has_exact_full(cands2):
                    mr = resolve_conv_context(text, subj, index, floor)
                    if mr is not None:
                        emit(source_type, source_id, thread_id, mr)
                        ctx2 += 1
                        continue
                # Neither anchor nor context resolved — fall back to pass-1 aggregate.
                mr = aggregate(cands2, resolve_threshold, margin, floor)
                if mr is not None:
                    emit(source_type, source_id, thread_id, mr)
                    if mr.status == "resolved":
                        thread_subjects[thread_id].add(mr.name_key)
            print(f"  pass 2 done: {anchored2} thread-anchor + {ctx2} context mentions")
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

    # --- match_lowercase flag (Experiment A: recover lowercase mentions) ---
    def resolve_lc(text):
        return aggregate(match_item(text, index, course_map, match_lowercase=True),
                         0.80, 0.10, 0.55)
    # a fully-lowercase full name is invisible by default, recovered with the flag
    lc_full = distinctive.name_key  # already lowercase name_key
    r = resolve(f"i had {lc_full} for class and it was great")
    check("golden(lc): lowercase full name missed without flag",
          r is None or r.name_key != distinctive.name_key)
    r = resolve_lc(f"i had {lc_full} for class and it was great")
    check("golden(lc): lowercase full name resolves with flag",
          r is not None and r.status == "resolved" and r.name_key == distinctive.name_key)
    # a corroborated lowercase surname (first name nearby) resolves with the flag
    r = resolve_lc(f"{distinctive.first_name} {distinctive.last_name} was fair")
    check("golden(lc): corroborated lowercase surname resolves",
          r is not None and r.status == "resolved" and r.name_key == distinctive.name_key)
    # a BARE lowercase common-word surname must STILL be dropped (no flood)
    r = resolve_lc("you should check the law before signing the lease")
    check("golden(lc): bare lowercase common-word still not resolved",
          r is None or r.status != "resolved")
    # a bare lowercase distinctive surname (no corroboration) does not resolve
    r = resolve_lc(f"{distinctive.last_name} was a fair grader")
    check("golden(lc): bare lowercase distinctive surname does not resolve",
          r is None or r.status != "resolved")
    # a lowercase COMMON-WORD surname, even corroborated by first name, must NOT
    # resolve via lastname (e.g. "d hope", "christian law") — only via full name.
    cw_prof = next(
        (p for p in profs
         if p.last_name in _COMMON_WORD_SURNAMES and len(p.first_name) >= 2
         and len(index.by_last_name[p.last_name]) == 1),
        None)
    if cw_prof is not None:
        r = resolve_lc(f"i really {cw_prof.last_name} the {cw_prof.first_name} idea here")
        check("golden(lc): corroborated lowercase common-word not resolved via lastname",
              r is None or r.status != "resolved" or r.method == "exact_full")
    else:
        check("golden(lc): common-word-surname case (skipped, none single)", True)
    # a lowercase-only full name must NOT seed the thread-anchor cascade: its
    # exact_full candidate carries lc_origin=True so handle() skips seeding.
    lc_cands = match_item(f"i had {lc_full} last term", index, course_map,
                          match_lowercase=True)
    lc_full_cand = next((c for c in lc_cands
                         if c.method == "exact_full" and c.name_key == lc_full), None)
    check("golden(lc): lowercase full name is lc_origin (won't seed anchor)",
          lc_full_cand is not None and lc_full_cand.lc_origin)
    # but a CAPITALIZED full name still seeds (lc_origin False)
    cap_cands = match_item(f"I had {distinctive.name_key.title()} last term",
                           index, course_map, match_lowercase=True)
    cap_full_cand = next((c for c in cap_cands
                          if c.method == "exact_full" and c.name_key == lc_full), None)
    check("golden(lc): capitalized full name still seeds anchor (not lc_origin)",
          cap_full_cand is not None and not cap_full_cand.lc_origin)
    # aggregate() must propagate lc_origin so handle() skips thread_subjects too.
    r = aggregate(lc_cands, 0.80, 0.10, 0.55)
    check("golden(lc): aggregate propagates lc_origin (won't seed conv_context)",
          r is not None and r.status == "resolved" and r.lc_origin)

    # --- possessive ("X's") gates the surname in, same as bare "X" ---
    disp = distinctive.name_key.title()
    # full name + possessive resolves
    r = resolve(f"I loved {disp}'s class this term")
    check("golden(poss): full name with possessive resolves",
          r is not None and r.status == "resolved" and r.name_key == distinctive.name_key)
    # bare surname possessive produces the same candidate as bare surname (gated in)
    poss_c = match_item(f"{distinctive.last_name.title()}'s exam was fair",
                        index, course_map)
    bare_c = match_item(f"{distinctive.last_name.title()} exam was fair",
                        index, course_map)
    check("golden(poss): surname possessive gates same as bare surname",
          {c.name_key for c in poss_c} == {c.name_key for c in bare_c}
          and distinctive.name_key in {c.name_key for c in poss_c})
    # an apostrophe-surname professor still resolves WITH a possessive (not corrupted)
    ap = next((p for p in profs
               if "'" in p.last_name and len(index.by_last_name[p.last_name]) == 1
               and " " in p.name_key), None)
    if ap is not None:
        r = resolve(f"I had {ap.name_key.title()}'s class")
        check("golden(poss): apostrophe-surname prof survives possessive",
              r is not None and r.status == "resolved" and r.name_key == ap.name_key)
    else:
        check("golden(poss): apostrophe-surname case (skipped, none single)", True)


_SURNAME_WORD_RE = re.compile(r"[a-z][a-z'-]*")


def resolve_thread_anchor(
    text: str, thread_surnames: Dict[str, Set[str]], floor: float,
) -> Optional[MatchResult]:
    """Resolve a bare surname in `text` against full names seen in the thread.

    `thread_surnames` maps a surname -> set of full name_keys (from exact_full
    matches) that appeared anywhere in the thread. A bare surname in the comment
    (whole word, case-insensitive, length > 2, not a common-word surname, and
    not already part of a full name written in this comment) that matches:
      - exactly one thread full name -> resolved at 0.90 (method thread_anchor)
      - two or more -> ambiguous among them
    Returns None if nothing matches or below floor.
    """
    if not thread_surnames:
        return None
    norm = normalize_name(text)
    words = set(_SURNAME_WORD_RE.findall(norm))
    best: Optional[MatchResult] = None
    for surname, nks in thread_surnames.items():
        if len(surname) <= 2 or surname in _COMMON_WORD_SURNAMES:
            continue
        if surname not in words:
            continue
        # Skip if the comment already spells out the full name (that path is
        # exact_full in pass 1, not a bare-surname anchor).
        if any(nk in norm for nk in nks):
            continue
        ordered = sorted(nks)
        if len(ordered) == 1:
            conf = 0.90
            if conf < floor:
                return None
            return MatchResult(name_key=ordered[0], confidence=conf,
                               method="thread_anchor", matched_token=surname,
                               status="resolved")
        # multiple full names share this surname in-thread -> ambiguous
        if 0.90 >= floor and best is None:
            best = MatchResult(name_key="", confidence=0.90,
                               method="thread_anchor", matched_token=surname,
                               status="ambiguous", candidate_keys=ordered)
    return best


def has_anchorable_surname(text: str, index: "ProfessorIndex") -> bool:
    """True if `text` contains a word that is a known professor surname.

    Mirrors the gating in resolve_thread_anchor (whole word, length > 2, not a
    common-word surname) so it predicts whether the thread-anchor layer could
    plausibly use this item in pass 2. Used to decide whether an unmatched item
    is worth queueing — at queue-time the thread's surname set isn't fully
    populated yet, so we test against the full catalog instead.
    """
    for w in set(_SURNAME_WORD_RE.findall(normalize_name(text))):
        if len(w) > 2 and w not in _COMMON_WORD_SURNAMES and w in index.by_last_name:
            return True
    return False


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

    # --- Suffix-duplicate merge: same person catalogued with and without a
    # generational suffix collapses to ONE canonical professor (more ratings
    # wins), so a single mention can't emit both variants. ---
    dup_pair = [
        Professor("richard-melloni", "Richard Melloni", "richard melloni",
                  "Psychology", "COS", 30, 30, 9),
        Professor("richard-melloni-jr", "Richard Melloni Jr", "richard melloni jr",
                  "Psychology", "COS", 4, 4, 1),
    ]
    merged = merge_suffix_duplicates(dup_pair)
    check("suffix-dup: pair collapses to one professor", len(merged) == 1)
    check("suffix-dup: canonical is the higher-rated entry",
          merged[0].slug == "richard-melloni")
    # A distinct person who merely shares a surname is NOT merged.
    no_merge = merge_suffix_duplicates([
        Professor("a-jones", "Aaron Jones", "aaron jones", "CS", "Khoury", 5, 5, 1),
        Professor("b-jones-jr", "Brian Jones Jr", "brian jones jr", "CS", "Khoury", 5, 5, 1),
    ])
    check("suffix-dup: different first names not merged", len(no_merge) == 2)

    # --- Bug 1: a generational suffix is NOT the surname ---
    suf_fix = [
        Professor("martin-schwarz", "Martin Schwarz Jr.", "martin schwarz jr.",
                  "Mathematics", "COS", 10, 10, 3),
        Professor("olin-shivers", "Olin Shivers III", "olin shivers iii",
                  "Computer Science", "Khoury", 20, 20, 5),
    ]
    suf_idx = ProfessorIndex(suf_fix)
    check("suffix: last_name is real surname not 'jr.'",
          suf_fix[0].last_name == "schwarz")
    check("suffix: last_name is real surname not 'iii'",
          suf_fix[1].last_name == "shivers")
    check("suffix: prof indexed under real surname",
          len(suf_idx.by_last_name.get("schwarz", [])) == 1)
    check("suffix: prof NOT indexed under the suffix token",
          suf_idx.by_last_name.get("jr.") is None
          and suf_idx.by_last_name.get("iii") is None)
    # A bare "Jr" ending a capitalized run must not seed a suffix-named prof even
    # when the first name appears elsewhere ("Martin … MLK Jr"). The suffix is not
    # a surname, so it must never be the candidate anchor.
    suf_np = ProfessorIndex([
        Professor("martin-schwarz", "Martin Schwarz Jr", "martin schwarz jr",
                  "Mathematics", "COS", 10, 10, 3)])
    cands = match_item("Martin is away. Happy MLK Jr to all", suf_np, {})
    check("suffix: bare 'Jr' + stray first name does not match suffix prof",
          not any(c.name_key == "martin schwarz jr" for c in cands))
    # But the real surname still resolves with corroboration.
    cands = match_item("I had Martin Schwarz for calc and he was fair", suf_idx, {})
    check("suffix: real surname still resolves",
          any(c.name_key == "martin schwarz jr." and c.confidence >= 0.97 for c in cands))

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

    # --- Bug 4: first-name corroboration requires PROXIMITY. A first name far
    # from the surname is a coincidence ("George Floyd … Freddie Gray"); the real
    # mention has them together, allowing a middle name in between. ---
    near = _first_name_near
    check("proximity: adjacent first+last corroborates",
          near("i had john rachlin for ds", "john", "rachlin"))
    check("proximity: middle name still corroborates",
          near("thomas john plahovinsak teaches micro", "thomas", "plahovinsak"))
    check("proximity: scattered first+last does NOT corroborate",
          not near("george floyd ignited it freddie gray and others", "george", "gray"))
    check("proximity: far-apart same sentence does NOT corroborate",
          not near("michael was great and i also saw professor gray downtown last week too", "michael", "gray"))
    check("proximity: surname-before-first does not corroborate",
          not near("gray is tough and george is easy", "george", "gray"))

    # --- Bug 4 refinement: a UNIQUE distinctive surname (one catalog prof) may
    # still resolve on a scattered first name — there's no other prof to mis-pick,
    # so "Felleisen … matthias" elsewhere is safe. A COLLISION surname may not. ---
    uniq_idx = ProfessorIndex([
        Professor("matthias-felleisen", "Matthias Felleisen", "matthias felleisen",
                  "Computer Science", "Khoury", 100, 100, 40)])
    cands = match_item(
        "Matthias makes the curriculum. I recommend reading Felleisen's blog later",
        uniq_idx, {})
    check("unique surname: scattered first name still resolves",
          any(c.name_key == "matthias felleisen" and c.confidence >= 0.97
              for c in cands))
    # A bare unique surname (no first name anywhere) stays sub-threshold as before.
    cands = match_item("I recommend reading Felleisen's blog post on developers",
                       uniq_idx, {})
    check("unique surname: bare (no first name) stays sub-threshold",
          all(c.confidence < 0.80 for c in cands if c.method == "lastname"))
    # A COLLISION surname must NOT resolve on a scattered first name (george gray).
    coll_idx = ProfessorIndex([
        Professor("george-gray", "George Gray", "george gray", "Physics", "COS", 5, 5, 1),
        Professor("michael-gray", "Michael Gray", "michael gray", "Math", "COS", 5, 5, 1)])
    cands = match_item("George Floyd ignited it, Freddie Gray and others", coll_idx, {})
    check("collision surname: scattered first name does NOT resolve",
          not any(c.confidence >= 0.97 for c in cands))

    # --- Bug 2 (now via proximity): a common-word surname must not resolve on a
    # SCATTERED common first name. "Don King" needs the names together. ---
    king_fix = [Professor("don-king", "Don King", "don king",
                          "Mathematics", "COS", 8, 8, 2)]
    king_idx = ProfessorIndex(king_fix)
    # "don" appears only inside "don't"; "King" is Burger King -> must not resolve.
    cands = match_item("Berger King disappeared, don't tell me NEU lost it too",
                       king_idx, {})
    check("commonword: scattered 'don' + Burger King does not resolve",
          not any(c.confidence >= 0.80 for c in cands))
    # "Chris King" with a stray "don" elsewhere -> must not resolve to Don King.
    cands = match_item("Don't know that prof. Chris King is tops though", king_idx, {})
    check("commonword: stray 'don' does not corroborate a different King",
          not any(c.name_key == "don king" and c.confidence >= 0.80 for c in cands))
    # The contiguous full name still resolves (high precision).
    cands = match_item("I took linear algebra with Don King last fall", king_idx, {})
    check("commonword: contiguous full name still resolves",
          any(c.name_key == "don king" and c.confidence >= 0.97 for c in cands))
    # Course corroboration still lifts a common-word surname (legit "Park CS3650").
    park_idx = ProfessorIndex([Professor("john-park", "John Park", "john park",
                                         "Computer Science", "Khoury", 30, 30, 9)])
    cands = match_item("CS3650 Computer Systems with Park is a lot of work",
                       park_idx, {"CS3650": {"john park"}})
    check("commonword: course corroboration still resolves",
          any(c.name_key == "john park" and c.confidence >= 0.98 for c in cands))

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

    # --- select_emissions: policy-driven multi-emission ---
    two_full = [Candidate("adam ding", 1.0, "exact_full", "adam ding"),
                Candidate("bogume jang", 1.0, "exact_full", "bogume jang")]
    em = select_emissions(two_full, 0.80, 0.10, 0.55, policy="full_only")
    check("two full names -> 2 resolved", len(em) == 2 and all(e.status == "resolved" for e in em))
    check("two full names emit both keys",
          {e.name_key for e in em} == {"adam ding", "bogume jang"})

    mixed = [Candidate("adam ding", 1.0, "exact_full", "adam ding"),
             Candidate("john smith", 0.70, "lastname", "smith")]
    check("full_only emits only the full name",
          [e.name_key for e in select_emissions(mixed, 0.80, 0.10, 0.55, policy="full_only")] == ["adam ding"])
    check("any_resolved still only emits >=threshold",
          [e.name_key for e in select_emissions(mixed, 0.80, 0.10, 0.55, policy="any_resolved")] == ["adam ding"])

    check("full_plus_corroborated promotes 0.97 surnames",
          [e.name_key for e in select_emissions(
              [Candidate("jane kim", 0.97, "lastname", "kim"), Candidate("joe lee", 0.97, "lastname", "lee")],
              0.80, 0.10, 0.55, policy="full_plus_corroborated")] == ["jane kim", "joe lee"])

    check("lone full name -> 1 resolved",
          len(select_emissions([Candidate("jane kim", 1.0, "exact_full", "jane kim")], 0.80, 0.10, 0.55, "full_only")) == 1)

    two_bare = [Candidate("a smith", 0.70, "lastname", "smith"), Candidate("b smith", 0.70, "lastname", "smith")]
    eb = select_emissions(two_bare, 0.80, 0.10, 0.55, "full_only")
    check("two bare surnames -> 1 ambiguous", len(eb) == 1 and eb[0].status == "ambiguous")

    check("nothing above floor -> empty",
          select_emissions([Candidate("x", 0.40, "lastname", "x")], 0.80, 0.10, 0.55, "full_only") == [])

    check("promotable below floor is not emitted",
          select_emissions([Candidate("jane kim", 0.80, "lastname", "kim"),
                            Candidate("adam ding", 0.54, "exact_full", "adam ding")],
                           0.80, 0.10, 0.55, "full_only")[0].name_key == "jane kim")

    check("select_emissions propagates lc_origin",
          select_emissions([Candidate("k rachlin", 1.0, "exact_full", "k rachlin", lc_origin=True)],
                           0.80, 0.10, 0.55, "full_only")[0].lc_origin is True)

    # --- override guard: explicit full names beat thread conv_context ---
    ding_jang = [Candidate("adam ding", 1.0, "exact_full", "adam ding"),
                 Candidate("bogume jang", 1.0, "exact_full", "bogume jang")]
    check("c67dmhj: emits ding+jang not a thread guess",
          {e.name_key for e in select_emissions(ding_jang, 0.80, 0.10, 0.55, "full_only")}
          == {"adam ding", "bogume jang"})
    check("override guard predicate fires on exact_full", _has_exact_full(ding_jang) is True)
    check("override guard predicate false without exact_full",
          _has_exact_full([Candidate("x smith", 0.70, "lastname", "smith")]) is False)

    # --- common-word surname suppression (drop only clear non-prof context) ---
    sup = _suppress_common_word
    check("drop: burger king", sup("king", "where can I find a Burger King on campus"))
    check("drop: king husky", sup("king", "where is that King Husky statue"))
    check("drop: isabella stewart gardner", sup("stewart", "free admission Isabella Stewart Gardner museum"))
    check("drop: law library", sup("law", "go to the Law library it is quiet"))
    check("drop: willis gym", sup("willis", "pickup volleyball in Willis anyone"))
    check("drop: price negotiable", sup("price", "selling a ticket price is negotiable dm me"))
    check("keep: prof price bro", not sup("price", "Prof. Price is such a bro in MUSC1112"))
    check("keep: professor adams", not sup("adams", "professor Adams Brookelyn for econ 3416"))
    check("keep: nik brown course", not sup("brown", "CS4300 with Nik Brown anyone taken it"))
    check("keep: plain surname review", not sup("king", "King is a great lecturer took him last fall"))
    check("suppress fires on bare worth it", sup("green", "is it worth it to take green"))
    check("keep: courteous is not court (word boundary)", not sup("black", "Benjamin Black is courteous and kind"))
    check("keep: his class is a prof signal", not sup("king", "his class with king was great"))
    check("keep: exam is a prof signal", not sup("price", "the price exam was brutal"))

    # --- Bug 3: author / building / citation contexts are not professors ---
    # A surname landing on a textbook author line, a campus building, or a legal
    # citation must not resolve, even with a stray first name.
    sup2 = _suppress_nonperson
    check("nonperson: textbook author + ISBN",
          sup2("massey", "Author: David B. Massey ISBN-10: 0-9842071-3-9 ©2012"))
    check("nonperson: textbook author + edition",
          sup2("gilbert", "CHEM 1161: Gilbert, Kirss, Bretz. Chemistry, 4th edition"))
    check("nonperson: Matthews Arena building",
          sup2("matthews", "Sitting in the DogHouse at Matthews Arena for a hockey game"))
    check("nonperson: legal citation X v. Y",
          sup2("fang", "restrictions established in Fang v. ICE, 935 F.3d 172 (3rd Cir. 2019)"))
    check("nonperson: Matthew's Arena (possessive) building",
          sup2("matthews", "R.I.P. Matthew's Arena! Been nice knowing you"))
    check("nonperson: keeps a real prof mention",
          not sup2("durant", "Prof. Durant's review for CS 5200 was helpful"))
    check("nonperson: keeps plain surname review",
          not sup2("massey", "I had Massey last semester and he was a great lecturer"))

    auth_fix = [Professor("david-massey", "David Massey", "david massey",
                          "Mathematics", "COS", 6, 6, 2)]
    auth_idx = ProfessorIndex(auth_fix)
    cands = match_item("Author: David B. Massey ISBN-10: 0-9842071-3-9 ©2012",
                       auth_idx, {})
    check("nonperson: textbook-author full name does not resolve",
          not any(c.confidence >= 0.80 for c in cands))
    # A CONTIGUOUS full name on an author line is suppressed in Layer 1 too.
    massey2_fix = [Professor("david-massey", "David Massey", "david massey",
                             "Mathematics", "COS", 6, 6, 2)]
    massey2_idx = ProfessorIndex(massey2_fix)
    cands = match_item("Selling: David Massey, Calculus 4th edition, ISBN 978-0",
                       massey2_idx, {})
    check("nonperson: contiguous author full name does not resolve",
          not any(c.confidence >= 0.80 for c in cands))
    arena_fix = [Professor("nicolle-matthews", "Nicolle Matthews", "r. nicolle matthews",
                           "Music", "Arts", 4, 4, 1)]
    arena_idx = ProfessorIndex(arena_fix)
    cands = match_item("R.I.P. Matthews Arena! Best hockey memories", arena_idx, {})
    check("nonperson: building name does not resolve",
          not any(c.method == "lastname" and c.confidence >= 0.80 for c in cands))

    check("strip t3", strip_fullname_prefix("t3_9z0c3") == "9z0c3")
    check("strip t1", strip_fullname_prefix("t1_abc") == "abc")

    check("conv trigger prof initial", has_context_trigger("I think Prof K is fair"))
    check("conv trigger pronoun", has_context_trigger("honestly he is the worst"))
    check("conv no trigger", not has_context_trigger("the weather is nice today"))

    # Pass-2 queue gate: a known bare surname is anchorable even with no trigger
    # (thread_anchor can resolve it), so it must still be queued. This is what
    # keeps the bare-surname recall path alive — a trigger-only filter would
    # wrongly drop it.
    check("anchorable known surname", has_anchorable_surname("kuznetsov is brutal", idx))
    check("anchorable rejects unknown word", not has_anchorable_surname("the exam was hard", idx))
    check("anchorable rejects short word", not has_anchorable_surname("li is here", idx))
    check("queue gate keeps bare surname (no trigger)",
          not has_context_trigger("kuznetsov is brutal")
          and has_anchorable_surname("kuznetsov is brutal", idx))
    check("queue gate drops hopeless item",
          not has_context_trigger("the parking was terrible")
          and not has_anchorable_surname("the parking was terrible", idx))

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

    # Thread surname anchor: bare "kuznetsov" with the full name in the thread map.
    tsm = {"kuznetsov": {"anatoliy kuznetsov"}}
    r = resolve_thread_anchor("kuznetsov is chill, take his class", tsm, 0.55)
    check("anchor resolves bare surname", r is not None and r.status == "resolved"
          and r.name_key == "anatoliy kuznetsov" and r.method == "thread_anchor"
          and r.confidence == 0.90)
    # lowercase too
    r = resolve_thread_anchor("im taking KUZNETSOV rn", tsm, 0.55)
    check("anchor case-insensitive", r is not None and r.status == "resolved")
    # two same-surname full names in thread -> ambiguous
    tsm2 = {"kim": {"jane kim", "david kim"}}
    r = resolve_thread_anchor("kim was tough", tsm2, 0.55)
    check("anchor two fullnames ambiguous", r is not None and r.status == "ambiguous"
          and set(r.candidate_keys) == {"jane kim", "david kim"})
    # surname not in thread -> None
    r = resolve_thread_anchor("smith was great", tsm, 0.55)
    check("anchor no surname match -> None", r is None)
    # stoplisted surname even if in thread map -> not anchored
    r = resolve_thread_anchor("the law was clear", {"law": {"john law"}}, 0.55)
    check("anchor skips stoplisted surname", r is None)
    # comment that already has the full name -> not anchored (exact_full handles it)
    r = resolve_thread_anchor("anatoliy kuznetsov is chill", tsm, 0.55)
    check("anchor skips when full name present", r is None)

    print(f"\n  {'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return failures


def experiment(args: argparse.Namespace) -> None:
    """Compare promotion policies on a fixed corpus head; print a comparison table
    and write a stratified hand-check sample per policy. Pass-1 emission only — the
    policy affects only pass-1, so this isolates the comparison and stays fast."""
    profs = load_catalog(args.backup)
    index = ProfessorIndex(profs)
    course_map = load_course_map(TRACE_COURSES_CSV, index)

    items = []  # (source_type, source_id, thread_id, text)
    for row in read_csv_rows(POSTS_CSV, args.sample):
        items.append(("post", row.get("id", ""), row.get("id", ""), _post_text(row)))
    for row in read_csv_rows(COMMENTS_CSV, args.sample):
        items.append(("comment", row.get("id", ""),
                      strip_fullname_prefix(row.get("link_id", "")), row.get("body", "")))
    print(f"experiment over {len(items)} items (cap {args.sample} per source)\n")

    # match_item is policy-agnostic; compute candidates once per item, reuse per policy.
    precomputed = [(st, sid, text, match_item(text, index, course_map, args.match_lowercase))
                   for st, sid, tid, text in items]

    rows = []
    for policy in PROMOTION_POLICIES:
        resolved = multi_rows = multi_items = ambiguous = 0
        sample_rows = []
        for st, sid, text, cands in precomputed:
            ems = select_emissions(cands, args.resolve_threshold, args.margin,
                                   args.floor, policy)
            res = [e for e in ems if e.status == "resolved"]
            if len(res) > 1:
                multi_items += 1
                multi_rows += len(res)
            resolved += len(res)
            ambiguous += sum(1 for e in ems if e.status == "ambiguous")
            if res:
                e = res[0]
                sample_rows.append((policy, st, sid, e.name_key, e.method, text[:160]))
        rows.append((policy, resolved, multi_items, multi_rows, ambiguous))
        with open(f"/tmp/experiment_sample_{policy}.tsv", "w", encoding="utf-8") as f:
            f.write("policy\ttype\tid\tname_key\tmethod\ttext\n")
            for r in sample_rows[:50]:
                f.write("\t".join(str(x) for x in r) + "\n")

    print(f"{'policy':24s} {'resolved':>9} {'multi_items':>12} "
          f"{'multi_rows':>11} {'ambiguous':>10}")
    for policy, resolved, mi, mr, amb in rows:
        print(f"{policy:24s} {resolved:>9} {mi:>12} {mr:>11} {amb:>10}")
    print("\nper-policy hand-check samples -> /tmp/experiment_sample_<policy>.tsv")


def main() -> None:
    p = argparse.ArgumentParser(description="Match Reddit mentions to professors.")
    p.add_argument("--backup", default=DEFAULT_BACKUP)
    p.add_argument("--resolve-threshold", type=float, default=0.80)
    p.add_argument("--margin", type=float, default=0.10)
    p.add_argument("--floor", type=float, default=0.55)
    p.add_argument("--no-conv-context", action="store_true")
    p.add_argument("--match-lowercase", action="store_true",
                   help="Also match fully-lowercase surnames (corroborated only); "
                        "recovers casual mentions the cap-token gate misses")
    p.add_argument("--limit", type=int)
    p.add_argument("--calibrate", action="store_true")
    p.add_argument("--selftest", action="store_true", help="Run offline unit checks and exit")
    p.add_argument("--experiment", action="store_true",
                   help="Compare promotion policies on a fixed sample; print a table.")
    p.add_argument("--policy", choices=PROMOTION_POLICIES, default="full_plus_corroborated",
                   help="Multi-name promotion policy for run() (default full_plus_corroborated).")
    p.add_argument("--sample", type=int, default=50000,
                   help="Per-source item cap for --experiment (deterministic head).")
    args = p.parse_args()

    if args.selftest:
        sys.exit(selftest())
    if args.calibrate:
        calibrate(args)
        return
    if args.experiment:
        experiment(args)
        return
    run(args)


if __name__ == "__main__":
    main()
