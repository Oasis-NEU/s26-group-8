"""Tests for the GOATED leaderboard's Bayesian ranking.

Ranking by raw avg_rating let a 5.00 from 3 reviews outrank a 4.9 from 500 --
276 professors sit at exactly 5.00 and 95% of them have fewer than 15 reviews.
The board now sorts on a shrunk posterior mean instead, while still *displaying*
avg_rating.
"""

import os

import pytest

os.environ.setdefault("CRDB_DATABASE_URL", "postgresql://stub")
import server

# An arbitrary but realistic prior for the arithmetic below. It is NOT the live
# value and must not be read as one: measured on the 2026-08-03 corpus the prior
# is 4.452, and it moves whenever the corpus is re-scraped or the blend changes
# (this constant said 4.169 before single-source calibration raised every RMP-only
# rating). ranking_prior() reads the real one; nothing here should hardcode it.
C = 4.169


def rank(profs):
    """Order professors the way the SQL does: shrunk score desc, reviews desc."""
    return sorted(
        profs,
        key=lambda p: (server.shrunk_score(p[1], p[2], C), p[2]),
        reverse=True,
    )


# ── the maths ───────────────────────────────────────────────────────────────

def test_low_n_perfect_score_is_dragged_toward_the_mean():
    # 5.00 from 3 reviews carries almost no evidence.
    assert server.shrunk_score(5.0, 3, C) == pytest.approx(4.216, abs=0.001)


def test_high_n_score_barely_moves():
    # 4.96 from 454 reviews is well evidenced, so it should stay near 4.96.
    assert server.shrunk_score(4.96, 454, C) == pytest.approx(4.882, abs=0.001)


def test_more_reviews_means_less_shrinkage():
    a = server.shrunk_score(4.9, 20, C)
    b = server.shrunk_score(4.9, 500, C)
    assert b > a, "the better-evidenced professor must score higher"


def test_zero_reviews_collapses_to_the_prior():
    assert server.shrunk_score(4.9, 0, C) == pytest.approx(C)


def test_none_rating_returns_none():
    assert server.shrunk_score(None, 100, C) is None


def test_null_review_count_is_treated_as_zero():
    assert server.shrunk_score(4.9, None, C) == pytest.approx(C)


# ── the ranking this exists to fix ──────────────────────────────────────────

def test_three_review_five_star_no_longer_beats_a_well_reviewed_professor():
    # (name, avg_rating, total_reviews) — the exact Law/ProfStudies failure.
    ghost = ("Perfect, 3 reviews", 5.0, 3)
    real = ("Excellent, 500 reviews", 4.90, 500)
    assert rank([ghost, real])[0] is real


def test_ordering_among_well_reviewed_professors_is_preserved():
    # Shrinkage must not reshuffle professors who all have plenty of evidence.
    profs = [("a", 4.96, 454), ("b", 4.90, 400), ("c", 4.85, 300)]
    assert [p[0] for p in rank(profs)] == ["a", "b", "c"]


def test_a_slightly_lower_but_far_better_evidenced_score_can_win():
    # 4.81 from 1518 reviews outranks 4.89 from 224 — the intended correction.
    low_n = ("4.89 / 224", 4.89, 224)
    high_n = ("4.81 / 1518", 4.81, 1518)
    assert rank([low_n, high_n])[0] is high_n


def test_shrinkage_cannot_widen_the_top_band():
    # It is a contraction: for equal n the gap shrinks by n/(n+m). This is why
    # the fix is the sort key and not the displayed number.
    raw_gap = 4.96 - 4.85
    shrunk_gap = server.shrunk_score(4.96, 400, C) - server.shrunk_score(4.85, 400, C)
    assert shrunk_gap < raw_gap


# ── wiring ──────────────────────────────────────────────────────────────────

def test_ranking_sql_is_used_by_the_leaderboard_query():
    src = server.goat_professors.__doc__ or ""
    import inspect
    body = inspect.getsource(server.goat_professors)
    assert "RANKING_SCORE_SQL" in body, "leaderboard must sort on the shrunk score"
    assert "ORDER BY avg_rating" not in body, "raw avg_rating ordering must be gone"


def test_ranking_sql_reads_the_prior_from_the_data():
    # Hardcoding C would drift every time the corpus is re-scraped.
    assert str(server.SHRINKAGE_M) in server.RANKING_SCORE_SQL


# ── the prior, measured over professors who actually have a measurement ─────
#
# C was avg(avg_rating) over the whole catalog, which is mostly professors with a
# handful of responses: their ratings are noise, and averaging noise gives a
# number that describes the noise, not the professors. The prior of a ranking
# should be the mean of the quantity being ranked, so it is measured over
# professors whose rating is actually pinned down.

CATALOG = [   # (avg_rating, total_reviews)
    (5.00, 2), (5.00, 3), (1.00, 1), (5.00, 4),     # thin: noise, not measurements
    (4.20, 300), (4.30, 250), (4.10, 400),          # well evidenced
]


def prior_query_one(sql, params):
    """Stands in for the DB, honouring the review floor the query passes."""
    floor = params[0]
    kept = [rating for rating, n in CATALOG if n >= floor]
    return {"prior": sum(kept) / len(kept)} if kept else {"prior": None}


def test_prior_ignores_professors_with_too_little_evidence():
    # The well-evidenced mean is 4.20; the whole-catalog mean is 4.09 and is
    # decided by four professors with ten responses between them.
    assert server.ranking_prior(prior_query_one) == pytest.approx(4.20, abs=0.005)


def test_prior_passes_its_evidence_floor_to_the_query():
    seen = {}

    def spy(sql, params):
        seen["params"] = params
        return {"prior": 4.2}

    server.ranking_prior(spy)
    assert seen["params"] == (server.RANKING_PRIOR_MIN_REVIEWS,)


def test_prior_falls_back_when_nothing_is_well_evidenced():
    # An empty or brand-new catalog must not divide by zero or rank on None.
    assert server.ranking_prior(lambda sql, params: {"prior": None}) == \
        pytest.approx(server.FALLBACK_PRIOR)
    assert server.ranking_prior(lambda sql, params: None) == \
        pytest.approx(server.FALLBACK_PRIOR)


def test_prior_is_not_college_specific():
    # A per-college prior ranks professors against their own college, which is
    # department-relative ranking under another name — considered and rejected;
    # the board is a class-picking tool, not a per-department award.
    import inspect
    body = inspect.getsource(server.ranking_prior)
    assert "college" not in body


def test_leaderboard_measures_the_prior_rather_than_averaging_the_column():
    import inspect
    body = inspect.getsource(server.goat_professors)
    assert "ranking_prior(" in body
    assert "avg(avg_rating)" not in server.RANKING_SCORE_SQL, \
        "the prior is measured separately, not averaged inside the sort"


def test_leaderboard_returns_total_reviews():
    import inspect
    body = inspect.getsource(server.goat_professors)
    assert '"totalReviews"' in body, "Reviews column needs rated-response counts"


def test_cache_key_was_versioned():
    # A stale entry would serve the previous ordering after deploy — bumped for
    # the shrunk score, again when the prior changed, again for the single review
    # floor plus the name tiebreak, and again when the payload gained
    # rmpAdjusted (test_rating_calibration owns that last one).
    import inspect
    assert 'goat:v5:' in inspect.getsource(server.goat_professors)


# ── the review floor ────────────────────────────────────────────────────────
#
# One floor for every college, at the same 30 the prior uses. It replaced a floor
# of 100 carrying an exception that dropped Law and Professional Studies to 5.


def test_board_floor_matches_the_prior_floor():
    # Ranking someone the prior refuses to learn from is incoherent.
    assert server.BOARD_MIN_REVIEWS == server.RANKING_PRIOR_MIN_REVIEWS


def test_no_per_college_floor_exception():
    import inspect
    body = inspect.getsource(server.goat_professors)
    assert not hasattr(server, "NO_MIN_COLLEGES"), \
        "the per-college floor exception is what put 7-rating professors on a board"
    assert "college in" not in body, "no college may get its own evidence floor"


def test_floor_defaults_to_the_constant_not_a_literal():
    import inspect
    body = inspect.getsource(server.goat_professors)
    assert "BOARD_MIN_REVIEWS" in body
    assert '"100"' not in body, "the old 100 default must be gone"


def test_thin_professors_cannot_reach_a_board_even_at_a_perfect_score():
    # The Professional Studies failure: 4.96 from 7 reviews held rank 7. Below the
    # floor it is not eligible at all, and even if it were, shrinkage sinks it.
    assert 7 < server.BOARD_MIN_REVIEWS
    thin = server.shrunk_score(5.0, 7, C)
    real = server.shrunk_score(4.68, 96, C)
    assert real > thin, "96 reviews at 4.68 outweigh 7 at a perfect 5.00"


def test_the_floor_is_low_enough_not_to_be_the_thing_doing_the_excluding():
    # 100 excluded people the score already excluded. At 30 the score has to earn
    # it: a professor at the floor with a perfect 5.00 must still land below a
    # well-evidenced 4.9, or the floor is load-bearing again.
    at_floor = server.shrunk_score(5.0, server.BOARD_MIN_REVIEWS, C)
    established = server.shrunk_score(4.90, 500, C)
    assert established > at_floor


# ── determinism ─────────────────────────────────────────────────────────────

def test_ordering_breaks_ties_by_name():
    # Equal score and equal review count left the order to the scan, so a board
    # could reshuffle between two identical requests.
    import inspect
    body = inspect.getsource(server.goat_professors)
    assert "total_reviews DESC, name" in body


# ── comment counts must follow the TRACE name, not the RMP one ───────────────
# A fuzzy-matched professor's TRACE comments are filed under trace_name_key, not
# name_key (see professor_full.trace_key). The board counted both sides under
# name_key alone, so it reported zero TRACE comments for exactly the professors
# whose profile page resolves them correctly — Meg Heckman showed her handful of
# RMP comments and none of her TRACE ones.

FUZZY = {"slug": "meg-heckman", "name": "Meg Heckman", "department": "Journalism",
         "name_key": "meg heckman", "trace_name_key": "margaret heckman",
         "rmp_rating": 4.4, "trace_rating": 4.6, "avg_rating": 4.5,
         "total_reviews": 300}
EXACT = {"slug": "olin-guha", "name": "Olin Guha", "department": "Khoury",
         "name_key": "olin guha", "trace_name_key": None,
         "rmp_rating": 4.1, "trace_rating": 4.2, "avg_rating": 4.15,
         "total_reviews": 250}

# name_key -> comments, in each source's own key space.
RMP_COMMENTS = {"meg heckman": 12, "olin guha": 30}
TRACE_COMMENTS = {"margaret heckman": 900, "olin guha": 400}


def _leaderboard_query(catalog_rows):
    """Stands in for query(), answering each of the endpoint's statements."""
    def query(sql, params):
        s = " ".join(sql.split())
        if "FROM professors_catalog" in s:
            return catalog_rows
        if "FROM rmp_reviews" in s:
            return [{"name_key": k, "cnt": RMP_COMMENTS[k]}
                    for k in params if k in RMP_COMMENTS]
        if "FROM trace_comments" in s:
            return [{"name_key": k, "cnt": TRACE_COMMENTS[k]}
                    for k in params if k in TRACE_COMMENTS]
        raise AssertionError(f"unexpected query: {s}")
    return query


def _run_leaderboard(monkeypatch, catalog_rows):
    monkeypatch.setattr(server, "query", _leaderboard_query(catalog_rows))
    monkeypatch.setattr(server, "query_one", lambda sql, params: {"prior": C})
    monkeypatch.setattr(server, "cache_get", lambda key: None)
    monkeypatch.setattr(server, "cache_set", lambda key, val: None)
    with server.app.test_request_context("/api/goat-professors?college=Khoury"):
        resp = server.goat_professors()
    return {p["name"]: p for p in resp.get_json()}


def test_fuzzy_matched_professor_gets_her_trace_comments(monkeypatch):
    out = _run_leaderboard(monkeypatch, [FUZZY])
    # 12 RMP + 900 TRACE. Keying both on "meg heckman" would have given just 12.
    assert out["Meg Heckman"]["totalComments"] == 912


def test_exact_match_comment_count_is_unchanged(monkeypatch):
    out = _run_leaderboard(monkeypatch, [EXACT])
    # trace_name_key IS NULL -> both sides read name_key, the pre-existing case.
    assert out["Olin Guha"]["totalComments"] == 430


def test_each_side_is_queried_under_its_own_key(monkeypatch):
    seen = []
    base = _leaderboard_query([FUZZY, EXACT])

    def spy(sql, params):
        seen.append((" ".join(sql.split()), list(params)))
        return base(sql, params)

    monkeypatch.setattr(server, "query", spy)
    monkeypatch.setattr(server, "query_one", lambda sql, params: {"prior": C})
    monkeypatch.setattr(server, "cache_get", lambda key: None)
    monkeypatch.setattr(server, "cache_set", lambda key, val: None)
    with server.app.test_request_context("/api/goat-professors?college=Khoury"):
        server.goat_professors()

    rmp_params = next(p for s, p in seen if "FROM rmp_reviews" in s)
    trace_params = next(p for s, p in seen if "FROM trace_comments" in s)
    assert sorted(rmp_params) == ["meg heckman", "olin guha"]
    # The TRACE side must ask for the TRACE spelling and must not ask for the RMP
    # one, which owns no trace_courses rows.
    assert sorted(trace_params) == ["margaret heckman", "olin guha"]
