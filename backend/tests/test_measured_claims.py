"""Guards the measured numbers that are written into comments.

Several comments around the leaderboard quote numbers measured off the live
corpus -- how many professors sit at exactly 5.00, how much of total_reviews is
TRACE, how far the comment count exceeds the rating count. They are load-bearing:
each one is the evidence for a design decision sitting right next to it, and a
reader who re-measures and gets something different has to work out whether the
code broke or the comment rotted.

They rot silently. Every one of them is a function of `professors_catalog`, which
`precompute.py` rebuilds wholesale, so a single precompute run can invalidate a
dozen comments in files nobody touched. That is exactly what happened on
2026-08-03: the total_reviews redefinition left six numbers wrong across
server.py and Homepage.tsx, one of them ("798 professors with no written RMP
review", actually 1,907) off by more than 2x.

So each claim is parsed back out of the source and re-measured here.

Two deliberate choices:

  - Tolerances are loose, and per claim. The failure being caught is "a reader
    would draw a different conclusion", not "the corpus moved". A comment saying
    276 when the answer is 277 misleads nobody, and a test that fails on it gets
    switched off, taking the 2x errors down with it.
  - The patterns are anchored on the surrounding prose, and each must match
    exactly once (test_every_claim_is_locatable). Rewording a guarded comment
    fails the suite rather than silently un-guarding it -- the alternative,
    matching a bare number, would quietly follow along to whatever else it hit.

Skips cleanly with no database, so CI without credentials is unaffected.
"""

import os
import pathlib
import re

import pytest

os.environ.setdefault("CRDB_DATABASE_URL", "postgresql://stub")
import server

REPO = pathlib.Path(__file__).resolve().parents[2]
SERVER_PY = REPO / "backend" / "server.py"
HOMEPAGE_TSX = REPO / "frontend" / "src" / "pages" / "Homepage.tsx"
GOAT_TEST = REPO / "backend" / "tests" / "test_goat_ranking.py"


# ── database ────────────────────────────────────────────────────────────────

def _live_url():
    """The real connection string, read from .env rather than the environment.

    Every DB-touching test module pins os.environ["CRDB_DATABASE_URL"] to
    "postgresql://stub" via setdefault, and modules are collected alphabetically,
    so by the time this one runs the variable is almost always the stub --
    load_dotenv() will not override an existing value. dotenv_values() parses the
    file without touching os.environ, which keeps that stub intact for everyone
    else while still letting this module reach the real corpus.
    """
    try:
        from dotenv import dotenv_values
    except ImportError:
        return None
    url = dotenv_values(REPO / "backend" / ".env").get("CRDB_DATABASE_URL")
    if url:
        return url
    env_url = os.getenv("CRDB_DATABASE_URL")
    return None if env_url == "postgresql://stub" else env_url


class Corpus:
    """Thin query helper, plus the derived quantities claims are measured against."""

    def __init__(self, conn):
        import psycopg2.extras
        self._cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def rows(self, sql, params=None):
        self._cur.execute(sql, params)
        return self._cur.fetchall()

    def one(self, sql, params=None):
        rows = self.rows(sql, params)
        return rows[0] if rows else None

    def val(self, sql, params=None):
        row = self.one(sql, params)
        return None if row is None else next(iter(row.values()))

    @property
    def prior(self):
        # server's own function, so the test ranks the way production ranks
        # instead of re-deriving a formula that could drift away from it.
        if not hasattr(self, "_prior"):
            self._prior = server.ranking_prior(self.one)
        return self._prior

    @property
    def colleges(self):
        return [r["college"] for r in self.rows(
            "SELECT DISTINCT college FROM professors_catalog "
            "WHERE college IS NOT NULL ORDER BY college")]

    def board(self, college, floor=None, limit=10):
        floor = server.BOARD_MIN_REVIEWS if floor is None else floor
        return self.rows(
            f"SELECT * FROM professors_catalog WHERE college = %s "
            f"AND total_reviews >= %s "
            f"ORDER BY {server.RANKING_SCORE_SQL} DESC NULLS LAST, "
            f"total_reviews DESC, name LIMIT %s",
            (college, floor, self.prior, limit))

    @property
    def all_board_rows(self):
        if not hasattr(self, "_rows"):
            self._rows = [r for c in self.colleges for r in self.board(c)]
        return self._rows

    @property
    def comment_ratios(self):
        """comments / ratings for every row on every board."""
        if hasattr(self, "_ratios"):
            return self._ratios
        rows = self.all_board_rows
        name_keys = [r["name_key"] for r in rows]
        trace_keys = list({server.trace_key(r) for r in rows})
        rmp = {r["name_key"]: int(r["c"]) for r in self.rows(
            "SELECT name_key, COUNT(*) c FROM rmp_reviews WHERE name_key IN %s "
            "AND comment IS NOT NULL AND comment != %s GROUP BY name_key",
            (tuple(name_keys), ""))}
        trace = {r["name_key"]: int(r["c"]) for r in self.rows(
            "SELECT tc2.name_key, COUNT(*) c FROM trace_comments tc "
            "JOIN trace_courses tc2 ON tc.tc_course_id = tc2.course_id "
            "  AND tc.tc_instructor_id = tc2.instructor_id "
            "  AND tc.tc_term_id = tc2.term_id "
            "WHERE tc2.name_key IN %s AND tc.comment IS NOT NULL "
            "  AND tc.comment != %s GROUP BY tc2.name_key",
            (tuple(trace_keys), ""))}
        self._ratios = [
            (rmp.get(r["name_key"], 0) + trace.get(server.trace_key(r), 0))
            / r["total_reviews"]
            for r in rows if r["total_reviews"]]
        return self._ratios

    def rating_inversions(self, college):
        """Adjacent pairs where the displayed rating goes up as rank goes down."""
        board = self.board(college)
        return sum(1 for a, b in zip(board, board[1:])
                   if (a["avg_rating"] or 0) < (b["avg_rating"] or 0))


@pytest.fixture(scope="module")
def corpus():
    url = _live_url()
    if not url:
        pytest.skip("no CRDB_DATABASE_URL in backend/.env -- claims unverified")
    try:
        import psycopg2
        conn = psycopg2.connect(url, sslmode="require", connect_timeout=15)
        # Autocommit because the connection is shared by every claim in the
        # module and all of this is read-only. Without it one failing query
        # aborts the transaction and every subsequent claim dies with
        # InFailedSqlTransaction instead of its own result, which turns a single
        # broken measurement into twenty identical unhelpful failures.
        conn.autocommit = True
    except Exception as exc:                      # noqa: BLE001 - any failure skips
        pytest.skip(f"database unreachable -- claims unverified ({exc})")
    try:
        yield Corpus(conn)
    finally:
        conn.close()


# ── the claims ──────────────────────────────────────────────────────────────
# (label, file, pattern with one capture group, measure, tolerance)
#
# Tolerance is ("rel", fraction) or ("abs", amount). Percentages and small
# multiples take absolute tolerances: 5% of "95%" is nearly five points, which
# would wave through a genuinely misleading number.

CLAIMS = [
    ("professors at exactly 5.00", SERVER_PY,
     r"\b(\d+) professors sit at exactly 5\.00",
     lambda c: c.val("SELECT count(*) FROM professors_catalog WHERE avg_rating = 5.0"),
     ("rel", 0.10)),

    ("share of those under 15 reviews", SERVER_PY,
     r"and (\d+)% of them have under 15 reviews",
     lambda c: 100.0 * c.val(
         # Both sides cast: CockroachDB has no implicit numeric coercion, and
         # count(*) comes back DECIMAL, so a bare float/count division fails with
         # "unsupported binary operator: <float> / <decimal>".
         "SELECT count(*) FILTER (WHERE total_reviews < 15)::float "
         "     / count(*)::float "
         "FROM professors_catalog WHERE avg_rating = 5.0"),
     ("abs", 3)),

    ("reviews needed to reach Khoury rank 10", SERVER_PY,
     r"rank-10 score needs ~(\d+)\s*\n#\s*reviews",
     lambda c: _reviews_to_reach(c, "Khoury"),
     ("rel", 0.15)),

    ("Law professors eligible at a floor of 5", SERVER_PY,
     r"Law survives it on population \((\d+) eligible\)",
     lambda c: c.val("SELECT count(*) FROM professors_catalog WHERE college = 'Law' "
                     "AND avg_rating IS NOT NULL AND total_reviews >= 5"),
     ("rel", 0.10)),

    ("Professional Studies eligible at a floor of 5", SERVER_PY,
     r"Professional Studies had (\d+),",
     lambda c: c.val("SELECT count(*) FROM professors_catalog "
                     "WHERE college = 'Professional Studies' "
                     "AND avg_rating IS NOT NULL AND total_reviews >= 5"),
     ("abs", 3)),

    ("length of the Professional Studies board", SERVER_PY,
     r"At 30 that board is (\d+) professors long",
     lambda c: len(c.board("Professional Studies")),
     ("abs", 1)),

    ("TRACE share of total_reviews (server)", SERVER_PY,
     r"overall-question responses, ~(\d+)% the latter",
     lambda c: _trace_share(c),
     ("abs", 2)),

    ("TRACE share of total_reviews (frontend)", HOMEPAGE_TSX,
     r"survey responses \(~(\d+)% the latter\)",
     lambda c: _trace_share(c),
     ("abs", 2)),

    ("Matherne comment count", SERVER_PY,
     r"Matherne: ([\d,]+) comments vs [\d,]+ ratings",
     lambda c: _matherne(c)[0],
     ("rel", 0.10)),

    ("Matherne rating count", SERVER_PY,
     r"Matherne: [\d,]+ comments vs ([\d,]+) ratings",
     lambda c: _matherne(c)[1],
     ("rel", 0.10)),

    ("median comments-per-rating multiple", SERVER_PY,
     r"median ([\d.]+)x and ranging",
     lambda c: _median(c.comment_ratios),
     ("abs", 0.3)),

    ("lowest comments-per-rating multiple", SERVER_PY,
     r"ranging ([\d.]+)-[\d.]+x",
     lambda c: min(c.comment_ratios),
     ("abs", 0.3)),

    ("highest comments-per-rating multiple", SERVER_PY,
     r"ranging [\d.]+-([\d.]+)x",
     lambda c: max(c.comment_ratios),
     ("abs", 0.4)),

    ("score/review-count tie groups", SERVER_PY,
     r"\b(\d+) such groups across the catalog",
     lambda c: _tie_groups(c)[0],
     ("rel", 0.25)),

    ("professors involved in those ties", SERVER_PY,
     r"covering (\d+) professors",
     lambda c: _tie_groups(c)[1],
     ("rel", 0.25)),

    ("eligible professors with no written RMP review", HOMEPAGE_TSX,
     r"the latter\), and ([\d,]+) of the professors",
     lambda c: c.val(
         "SELECT count(*) FROM professors_catalog p WHERE p.total_reviews >= %s "
         "AND NOT EXISTS (SELECT 1 FROM rmp_reviews r WHERE r.name_key = p.name_key "
         "  AND r.comment IS NOT NULL AND r.comment != %s)",
         (server.BOARD_MIN_REVIEWS, "")),
     ("rel", 0.10)),

    ("fewest rating inversions on a board", HOMEPAGE_TSX,
     r"moves backwards between adjacent rows, (\d+)-\d+ times per board",
     lambda c: min(c.rating_inversions(x) for x in c.colleges),
     ("abs", 1)),

    ("most rating inversions on a board", HOMEPAGE_TSX,
     r"moves backwards between adjacent rows, \d+-(\d+) times per board",
     lambda c: max(c.rating_inversions(x) for x in c.colleges),
     ("abs", 1)),

    ("professors at exactly 5.00 (goat test docstring)", GOAT_TEST,
     r"\b(\d+) professors sit at exactly 5\.00",
     lambda c: c.val("SELECT count(*) FROM professors_catalog WHERE avg_rating = 5.0"),
     ("rel", 0.10)),
]


def _reviews_to_reach(corpus, college):
    """How many reviews a flawless 5.00 needs to reach a board's rank-10 score."""
    board = corpus.board(college)
    if len(board) < 10:
        return 0
    target = server.shrunk_score(
        board[9]["avg_rating"], board[9]["total_reviews"], corpus.prior)
    n = 1
    while server.shrunk_score(5.0, n, corpus.prior) < target:
        n += 1
        if n > 100_000:                            # unreachable target
            return n
    return n


def _trace_share(corpus):
    return 100.0 * corpus.val(
        "SELECT sum(trace_reviews)::float / sum(total_reviews)::float "
        "FROM professors_catalog WHERE total_reviews >= %s",
        (server.BOARD_MIN_REVIEWS,))


def _matherne(corpus):
    """(comments, ratings) for the professor the comment names as its example."""
    prof = corpus.one("SELECT * FROM professors_catalog WHERE name = %s",
                      ("Marguerite Matherne",))
    if prof is None:
        pytest.skip("Marguerite Matherne is no longer in the catalog")
    rmp = corpus.val("SELECT count(*) FROM rmp_reviews WHERE name_key = %s "
                     "AND comment IS NOT NULL AND comment != %s",
                     (prof["name_key"], ""))
    trace = corpus.val(
        "SELECT count(*) FROM trace_comments tc "
        "JOIN trace_courses tc2 ON tc.tc_course_id = tc2.course_id "
        "  AND tc.tc_instructor_id = tc2.instructor_id "
        "  AND tc.tc_term_id = tc2.term_id "
        "WHERE tc2.name_key = %s AND tc.comment IS NOT NULL AND tc.comment != %s",
        (server.trace_key(prof), ""))
    return rmp + trace, prof["total_reviews"]


def _tie_group_sizes(corpus, college=None):
    """One row per group tied on both score and review count, so `name` decides.

    Grouped by college as well as by score and review count, because each board
    is a single college: two professors in different colleges never compete for a
    slot, so a tie between them is not a tie the `name` tiebreak has to settle.
    Grouping globally instead reports 520 professors where the boards actually
    contain 127.

    Deliberately not wrapped in an outer count(*): that returns a single row
    holding the number of groups, which reads as one group of that size and gets
    the two numbers backwards.
    """
    where = "total_reviews >= %s AND avg_rating IS NOT NULL"
    params = [corpus.prior, server.BOARD_MIN_REVIEWS]
    if college is not None:
        where += " AND college = %s"
        params.append(college)
    return corpus.rows(
        f"SELECT count(*) c FROM ("
        f"  SELECT college, {server.RANKING_SCORE_SQL} s, total_reviews "
        f"  FROM professors_catalog WHERE {where}"
        f") t GROUP BY college, s, total_reviews HAVING count(*) > 1",
        tuple(params))


def _tie_groups(corpus):
    """(groups, professors involved)."""
    rows = _tie_group_sizes(corpus)
    return len(rows), sum(int(r["c"]) for r in rows)


def _median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _find(path, pattern):
    """(stated number, line) for a pattern that must appear exactly once."""
    text = path.read_text()
    matches = list(re.finditer(pattern, text))
    assert len(matches) == 1, (
        f"{path.name}: expected exactly 1 match for {pattern!r}, found "
        f"{len(matches)}. The guarded comment was reworded or removed -- update "
        f"the pattern in CLAIMS so the number stays guarded.")
    m = matches[0]
    return float(m.group(1).replace(",", "")), text[:m.start(1)].count("\n") + 1


IDS = [c[0] for c in CLAIMS]


@pytest.mark.parametrize("label,path,pattern,measure,tol", CLAIMS, ids=IDS)
def test_every_claim_is_locatable(label, path, pattern, measure, tol):
    """Fails on a reworded comment even with no database to check it against."""
    _find(path, pattern)


@pytest.mark.parametrize("label,path,pattern,measure,tol", CLAIMS, ids=IDS)
def test_claim_still_matches_the_corpus(label, path, pattern, measure, tol, corpus):
    stated, line = _find(path, pattern)
    actual = measure(corpus)
    assert actual is not None, f"{label}: measured nothing"

    kind, amount = tol
    slack = amount if kind == "abs" else abs(actual) * amount
    assert abs(stated - actual) <= slack, (
        f"\n{path.relative_to(REPO)}:{line} is stale.\n"
        f"  claim:    {label}\n"
        f"  says:     {stated:g}\n"
        f"  measured: {actual:g}\n"
        f"  tolerance: +/-{slack:g}\n"
        f"Rewrite the comment with the measured value. If precompute.py has just "
        f"run, expect several of these together.")


def test_comments_outnumber_ratings_on_every_board_row(corpus):
    """server.py states this outright as the reason the column shows ratings."""
    below = [r for r in corpus.comment_ratios if r < 1]
    assert not below, (
        f"{len(below)} board rows now have fewer comments than ratings. server.py "
        f"claims comments 'exceed ratings on every single row' as the argument for "
        f"the Ratings column -- that sentence needs rewriting.")


# Law and Professional Studies are excluded from the floor comparison because
# they never carried the 100 floor -- they were the two colleges the old code
# dropped to 5. Comparing them to a hypothetical 100 would test a floor that was
# never in force and always "fail".
NEVER_ON_THE_100_FLOOR = {"Law", "Professional Studies"}


def test_business_is_the_only_board_the_review_floor_changes(corpus):
    """server.py names Business rank 10 as the one row a 100 -> 30 floor moves."""
    moved = {}
    for college in set(corpus.colleges) - NEVER_ON_THE_100_FLOOR:
        strict = [r["name"] for r in corpus.board(college, floor=100)]
        actual = [r["name"] for r in corpus.board(college, floor=30)]
        if strict != actual:
            moved[college] = (strict, actual)
    assert set(moved) <= {"Business"}, (
        f"the review floor now changes boards server.py does not mention: "
        f"{sorted(set(moved) - {'Business'})}. The '100 was doing almost no work' "
        f"argument needs re-measuring.")


def test_no_tie_group_reaches_a_board_top_ten(corpus):
    """The claim that survives a refresh: ties exist, but none are visible.

    This replaced a per-college assertion (that Law had none) which failed on
    data drift alone the first time total_reviews moved — the comment it guarded
    even predicted that, and still named the college. Which college holds a tie
    is not a property of the code; whether a tie can reorder a board a reader
    actually sees is.

    Measured the way the endpoint measures it: the same ORDER BY, per college,
    limited to the same 10.
    """
    ranked = (f"{server.RANKING_SCORE_SQL} DESC NULLS LAST, total_reviews DESC, name")
    visible = []
    for row in corpus.rows(
            "SELECT DISTINCT college FROM professors_catalog "
            "WHERE college IS NOT NULL", ()):
        college = row["college"]
        top = corpus.rows(
            f"SELECT name, {server.RANKING_SCORE_SQL} s, total_reviews "
            f"FROM professors_catalog WHERE college = %s AND total_reviews >= %s "
            f"ORDER BY {ranked} LIMIT 10",
            (corpus.prior, college, server.BOARD_MIN_REVIEWS, corpus.prior))
        seen = {}
        for r in top:
            seen.setdefault((r["s"], r["total_reviews"]), []).append(r["name"])
        for names in seen.values():
            if len(names) > 1:
                visible.append((college, sorted(names)))
    assert not visible, (
        f"a tie now decides a visible board position: {visible}. The name "
        f"tiebreak keeps the order stable, but server.py claims no tie reaches "
        f"a top 10 — re-measure that claim.")
