"""The RMP->TRACE projection, and the leaderboard tooltip that displays it.

The board's rating tooltip showed RMP on RateMyProfessors' scale, TRACE on
TRACE's, and an "Avg Rating" pooled from *projected* RMP and TRACE. The three
numbers were in two different units, so no arithmetic reconciled them:

  - Alec Stubbs showed RMP 5.00, TRACE 5.00, Avg 4.99 (RMP 5.00 projects to 4.96)
  - John Rachlin showed RMP 4.63, TRACE 4.74, Avg 4.75 -- above both
  - 32 of the 55 two-source rows across the ten boards showed Avg exactly equal
    to TRACE while RMP differed, so RMP looked discarded

The endpoint now also returns the projected value, which brackets the blend: on
the live corpus avg_rating fell inside [projected RMP, TRACE] for all 1,708
two-source professors. These tests pin the projection, the fit shared with
precompute, and that bracketing property.
"""

import math
import os
import random

import pytest

os.environ.setdefault("CRDB_DATABASE_URL", "postgresql://stub")
import precompute
import rating_scale
import server

C = 4.169  # an arbitrary prior for the endpoint tests; not the live value


def _pairs(n, slope, intercept, noise=0.0, seed=1):
    """n (trace, rmp) pairs on a known line, optionally with noise."""
    rng = random.Random(seed)
    trace = [1.0 + 4.0 * i / (n - 1) for i in range(n)]
    rmp = [slope * t + intercept + rng.uniform(-noise, noise) for t in trace]
    return trace, rmp


# ── the shared fit must agree with the one precompute has always used ────────
# rating_scale.fit_rma is a pure-Python rewrite of what fit_calibration did with
# numpy, because server.py carries neither pandas nor numpy. A rewrite of a
# fitted statistic is only safe if something holds the two together.

def test_pure_python_fit_matches_the_numpy_fit_on_a_clean_line():
    trace, rmp = _pairs(60, 2.4, -6.9)
    assert rating_scale.fit_rma(trace, rmp) == pytest.approx(
        precompute.fit_calibration(trace, rmp), abs=1e-9)


def test_pure_python_fit_matches_the_numpy_fit_on_noisy_data():
    for seed in range(5):
        trace, rmp = _pairs(200, 2.4, -6.9, noise=0.6, seed=seed)
        assert rating_scale.fit_rma(trace, rmp) == pytest.approx(
            precompute.fit_calibration(trace, rmp), abs=1e-9), f"seed {seed}"


def test_fit_matches_the_two_spreads():
    # The defining property of RMA: projecting back must reproduce TRACE's own
    # spread, which is what makes the inverse-variance weights commensurable.
    trace, rmp = _pairs(200, 2.4, -6.9, noise=0.5, seed=7)
    slope, intercept = rating_scale.fit_rma(trace, rmp)
    projected = [(r - intercept) / slope for r in rmp]
    assert _sd(projected) == pytest.approx(_sd(trace), rel=0.01)


def _sd(xs):
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


# ── the fit's guard rails, mirroring precompute's ────────────────────────────

def test_too_few_pairs_is_not_a_fit():
    trace, rmp = _pairs(rating_scale.CALIBRATION_MIN_POINTS - 1, 2.4, -6.9)
    assert rating_scale.fit_rma(trace, rmp) is None


def test_exactly_the_minimum_pairs_is_a_fit():
    trace, rmp = _pairs(rating_scale.CALIBRATION_MIN_POINTS, 2.4, -6.9)
    assert rating_scale.fit_rma(trace, rmp) is not None


def test_an_inverted_relationship_is_not_a_fit():
    # RMA takes its sign from the correlation, so without the guard it would
    # happily return a positive slope for an inverted pair of scales.
    trace, rmp = _pairs(60, -2.4, 12.0)
    assert rating_scale.fit_rma(trace, rmp) is None


def test_no_spread_on_either_side_is_not_a_fit():
    assert rating_scale.fit_rma([4.0] * 60, [4.5] * 60) is None
    assert rating_scale.fit_rma([4.0] * 60, list(range(60))) is None


def test_a_flat_slope_is_not_a_fit():
    # Below CALIBRATION_MIN_SLOPE the inverse projection explodes.
    trace, rmp = _pairs(60, 0.2, 3.0)
    assert rating_scale.fit_rma(trace, rmp) is None


def test_mismatched_input_lengths_are_not_a_fit():
    trace, rmp = _pairs(60, 2.4, -6.9)
    assert rating_scale.fit_rma(trace, rmp[:-1]) is None


# ── the projection must agree with precompute's vectorised twin ──────────────

CAL = (2.409, -6.940)  # the live fit, near enough


@pytest.mark.parametrize("rmp", [1.0, 2.0, 3.1, 4.0, 4.63, 4.86, 5.0])
def test_projection_matches_precomputes_vectorised_clip(rmp):
    assert rating_scale.project_rmp(rmp, CAL) == pytest.approx(
        float(precompute.calibrate_rmp(rmp, CAL)), abs=1e-9)


def test_projection_clips_at_the_top_of_the_scale():
    # Reachable at the live fit: RMP 5.00 already projects to 4.96, and a wider
    # slope on a future re-scrape pushes the top of RMP's range past 5.
    assert rating_scale.project_rmp(9.0, CAL) == 5.0


def test_projection_clips_at_the_bottom_of_the_scale():
    # Not reachable at the live fit — RMP 0.00 projects to 2.88, and RMP does not
    # go below 1 anyway — so the guard is tested against a calibration where it
    # bites rather than by feeding an impossible rating.
    assert rating_scale.project_rmp(1.0, (1.0, 3.0)) == 1.0


def test_a_perfect_rmp_score_does_not_project_to_a_perfect_one():
    # The whole reason Alec Stubbs' 5.00 / 5.00 displayed an Avg of 4.99.
    assert rating_scale.project_rmp(5.0, CAL) < 5.0


def test_projection_raises_a_typical_rmp_score():
    # RMP runs ~0.8 lower than TRACE, so the projection is upward in the middle
    # of the scale even though it clips downward at the very top.
    assert rating_scale.project_rmp(4.0, CAL) > 4.0


def test_projection_of_none_is_none():
    assert rating_scale.project_rmp(None, CAL) is None


# ── the server reads the calibration from the catalog ────────────────────────
# precompute fits on `rating` and `trace_overall` filtered by num_ratings and
# trace_reviews. professors_catalog stores all four, so the fit is reproducible
# at request time without a schema change or a precompute run.

def _catalog_fit_rows(n=60):
    trace, rmp = _pairs(n, 2.4, -6.9)
    return [{"rmp_rating": r, "trace_rating": t} for t, r in zip(trace, rmp)]


def test_calibration_queries_only_well_evidenced_professors(monkeypatch):
    seen = {}

    def query(sql, params):
        seen["sql"] = " ".join(sql.split())
        seen["params"] = list(params)
        return _catalog_fit_rows()

    monkeypatch.setattr(server, "query", query)
    server.rating_calibration(query)
    assert "num_ratings >= %s" in seen["sql"]
    assert "trace_reviews >= %s" in seen["sql"]
    assert seen["params"] == [rating_scale.CALIBRATION_MIN_RMP,
                              rating_scale.CALIBRATION_MIN_TRACE]


def test_calibration_matches_a_direct_fit_of_the_same_rows():
    rows = _catalog_fit_rows()
    got = server.rating_calibration(lambda sql, params: rows)
    want = rating_scale.fit_rma([r["trace_rating"] for r in rows],
                                [r["rmp_rating"] for r in rows])
    assert got == pytest.approx(want, abs=1e-9)


def test_calibration_falls_back_on_an_empty_catalog():
    got = server.rating_calibration(lambda sql, params: [])
    assert got == rating_scale.FALLBACK_CALIBRATION


def test_calibration_falls_back_rather_than_fitting_too_few_rows():
    rows = _catalog_fit_rows(rating_scale.CALIBRATION_MIN_POINTS - 1)
    assert server.rating_calibration(
        lambda sql, params: rows) == rating_scale.FALLBACK_CALIBRATION


def test_calibration_skips_rows_missing_either_side():
    rows = _catalog_fit_rows() + [
        {"rmp_rating": None, "trace_rating": 4.5},
        {"rmp_rating": 4.5, "trace_rating": None},
    ]
    got = server.rating_calibration(lambda sql, params: rows)
    want = rating_scale.fit_rma(*zip(*[(r["trace_rating"], r["rmp_rating"])
                                       for r in rows if r["rmp_rating"] is not None
                                       and r["trace_rating"] is not None]))
    assert got == pytest.approx(want, abs=1e-9)


# ── the endpoint exposes the projected value ─────────────────────────────────

TWO_SOURCE = {"slug": "kaan-onarlioglu", "name": "Kaan Onarlioglu",
              "department": "Cybersecurity", "name_key": "kaan onarlioglu",
              "trace_name_key": None, "rmp_rating": 4.86, "trace_rating": 4.91,
              "avg_rating": 4.91, "total_reviews": 478}
RMP_ONLY = {"slug": "rmp-only", "name": "Rmp Only", "department": "Khoury",
            "name_key": "rmp only", "trace_name_key": None,
            "rmp_rating": 4.40, "trace_rating": None,
            "avg_rating": 4.66, "total_reviews": 90}
TRACE_ONLY = {"slug": "trace-only", "name": "Trace Only", "department": "Khoury",
              "name_key": "trace only", "trace_name_key": None,
              "rmp_rating": None, "trace_rating": 4.70,
              "avg_rating": 4.70, "total_reviews": 300}


def _run_leaderboard(monkeypatch, rows, fit_rows=None):
    fit_rows = _catalog_fit_rows() if fit_rows is None else fit_rows

    def query(sql, params):
        s = " ".join(sql.split())
        # The calibration statement is the only one filtering on num_ratings.
        if "num_ratings >= %s" in s:
            return fit_rows
        if "FROM professors_catalog" in s:
            return rows
        if "FROM rmp_reviews" in s or "FROM trace_comments" in s:
            return []
        raise AssertionError(f"unexpected query: {s}")

    monkeypatch.setattr(server, "query", query)
    monkeypatch.setattr(server, "query_one", lambda sql, params: {"prior": C})
    monkeypatch.setattr(server, "cache_get", lambda key: None)
    monkeypatch.setattr(server, "cache_set", lambda key, val: None)
    with server.app.test_request_context("/api/goat-professors?college=Khoury"):
        return {p["name"]: p for p in server.goat_professors().get_json()}


def test_two_source_professor_gets_the_projected_rmp(monkeypatch):
    out = _run_leaderboard(monkeypatch, [TWO_SOURCE])["Kaan Onarlioglu"]
    cal = server.rating_calibration(lambda sql, params: _catalog_fit_rows())
    assert out["rmpAdjusted"] == pytest.approx(
        round(rating_scale.project_rmp(4.86, cal), 2), abs=1e-9)


def test_the_projected_value_is_not_the_raw_one(monkeypatch):
    # If these were equal the extra tooltip row would be noise, and the bug
    # (three numbers in two unit systems) would still be on the page.
    out = _run_leaderboard(monkeypatch, [TWO_SOURCE])["Kaan Onarlioglu"]
    assert out["rmpAdjusted"] != out["rmpRating"]


def test_the_blend_sits_between_the_projected_rmp_and_trace(monkeypatch):
    # The property that makes the tooltip read as sane: no row may show an Avg
    # outside
    # the two numbers above it. Verified across all 1,708 live two-source rows.
    out = _run_leaderboard(monkeypatch, [TWO_SOURCE])["Kaan Onarlioglu"]
    lo, hi = sorted((out["rmpAdjusted"], out["traceRating"]))
    assert lo <= out["avgRating"] <= hi


def test_rmp_only_professor_reports_no_projection(monkeypatch):
    # avg_rating already *is* the projection for these, so a second copy of it
    # under a different label would suggest a blend that never happened.
    out = _run_leaderboard(monkeypatch, [RMP_ONLY])["Rmp Only"]
    assert out["rmpAdjusted"] is None


def test_trace_only_professor_reports_no_projection(monkeypatch):
    out = _run_leaderboard(monkeypatch, [TRACE_ONLY])["Trace Only"]
    assert out["rmpAdjusted"] is None


def test_a_fallback_calibration_still_yields_a_projection(monkeypatch):
    # A catalog too thin to fit must not blank the tooltip row.
    out = _run_leaderboard(monkeypatch, [TWO_SOURCE], fit_rows=[])["Kaan Onarlioglu"]
    assert out["rmpAdjusted"] == pytest.approx(
        round(rating_scale.project_rmp(4.86, rating_scale.FALLBACK_CALIBRATION), 2))


def test_the_cache_key_moved_with_the_payload(monkeypatch):
    # An unbumped key serves the previous payload after deploy -- which here
    # means a tooltip with no projected row and no way to tell why.
    keys = []
    monkeypatch.setattr(server, "query", lambda sql, params: (
        _catalog_fit_rows() if "num_ratings >= %s" in " ".join(sql.split())
        else [TWO_SOURCE] if "FROM professors_catalog" in sql else []))
    monkeypatch.setattr(server, "query_one", lambda sql, params: {"prior": C})
    monkeypatch.setattr(server, "cache_get", lambda key: keys.append(key) or None)
    monkeypatch.setattr(server, "cache_set", lambda key, val: None)
    with server.app.test_request_context("/api/goat-professors?college=Khoury"):
        server.goat_professors()
    assert any(k.startswith("goat:v5:") for k in keys), keys


# ── precompute still behaves as it did ───────────────────────────────────────

def test_precompute_still_falls_back_through_the_shared_thresholds():
    trace, rmp = _pairs(rating_scale.CALIBRATION_MIN_POINTS - 1, 2.4, -6.9)
    assert precompute.fit_calibration(
        trace, rmp) == rating_scale.FALLBACK_CALIBRATION


def test_precompute_reexports_the_thresholds_it_always_had():
    # test_rating_blend.py and measure_calibration read these off precompute.
    assert precompute.CALIBRATION_MIN_RMP == rating_scale.CALIBRATION_MIN_RMP
    assert precompute.CALIBRATION_MIN_TRACE == rating_scale.CALIBRATION_MIN_TRACE
    assert precompute.FALLBACK_CALIBRATION == rating_scale.FALLBACK_CALIBRATION
