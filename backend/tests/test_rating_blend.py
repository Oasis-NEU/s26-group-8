"""Tests for the displayed rating blend (calibrate, then pool by precision).

Background: avg_rating used to be (rmp_rating + trace_rating) / 2, which averaged
two measurements on different scales while ignoring how many responses stood
behind each. One RMP review counted as much as 300 TRACE responses, so Eno Ebong
displayed 2.81 off a single 1-star against 300 TRACE responses at 4.62; 135
professors were shown more than a full point from what their evidence supported.

The replacement projects RMP onto the TRACE scale with a fit refit each run, then
pools by inverse variance. See docs/rating-blend-calibration.md.

No database: every function here is pure, driven by synthetic arrays.
"""

import numpy as np
import pandas as pd
import pytest

from precompute import (
    CALIBRATION_MIN_POINTS,
    CALIBRATION_MIN_RMP,
    CALIBRATION_MIN_TRACE,
    FALLBACK_CALIBRATION,
    FALLBACK_VARIANCES,
    apply_blended_rating,
    blend_ratings,
    calibrate_rmp,
    fit_calibration,
    measure_calibration,
    measure_variances,
    rmp_response_variance,
    trace_response_variance,
)
from rating_scale import pool_ratings, project_rmp, rmp_weight_per_rating

# Roughly the real measured values, so the numbers in these tests are realistic.
CAL = (1.90, -4.71)
VARS = (1.635, 0.534)


# ── the fit ─────────────────────────────────────────────────────────────────

def test_fit_recovers_a_known_relationship():
    trace = np.linspace(3.0, 5.0, 60)
    rmp = 1.9 * trace - 4.71
    slope, intercept = fit_calibration(trace, rmp)
    assert slope == pytest.approx(1.9, abs=1e-6)
    assert intercept == pytest.approx(-4.71, abs=1e-6)


def test_fit_tolerates_noise():
    rng = np.random.default_rng(0)
    trace = rng.uniform(3.0, 5.0, 400)
    rmp = 1.9 * trace - 4.71 + rng.normal(0, 0.3, 400)
    slope, _ = fit_calibration(trace, rmp)
    assert slope == pytest.approx(1.9, abs=0.2)


def test_fit_matches_the_two_spreads():
    # The contract that makes the inverse projection valid: applying the fit
    # backwards must land RMP on TRACE's spread, not a stretched version of it.
    # An OLS fit fails this — it shrinks the slope by the correlation, so the
    # inverse comes out 1/corr too wide.
    rng = np.random.default_rng(3)
    trace = rng.normal(4.3, 0.37, 500)
    rmp = 1.9 * trace - 4.71 + rng.normal(0, 0.45, 500)
    cal = fit_calibration(trace, rmp)
    projected = calibrate_rmp(rmp, cal)
    assert np.std(projected, ddof=1) == pytest.approx(np.std(trace, ddof=1), rel=0.05)


def test_inverted_relationship_falls_back():
    # RMA takes its sign from the correlation, so a negative relationship would
    # otherwise yield a confidently positive slope.
    rng = np.random.default_rng(4)
    trace = rng.uniform(3.0, 5.0, 200)
    rmp = -1.9 * trace + 12.0 + rng.normal(0, 0.2, 200)
    assert fit_calibration(trace, rmp) == FALLBACK_CALIBRATION


def test_uncorrelated_sources_fall_back():
    # No relationship to invert; the sd ratio would still return a plausible
    # number and quietly project noise onto the TRACE scale.
    rng = np.random.default_rng(5)
    assert fit_calibration(rng.uniform(3.0, 5.0, 300),
                           rng.uniform(1.0, 5.0, 300)) == FALLBACK_CALIBRATION


def test_too_few_points_falls_back():
    n = CALIBRATION_MIN_POINTS - 1
    trace = np.linspace(3.0, 5.0, n)
    assert fit_calibration(trace, 1.9 * trace - 4.71) == FALLBACK_CALIBRATION


def test_no_spread_in_trace_falls_back():
    # Every professor at the same TRACE score -> nothing to regress against.
    assert fit_calibration([4.5] * 100, np.linspace(2, 5, 100)) == FALLBACK_CALIBRATION


def test_flat_slope_falls_back():
    # A slope near zero would make the inverse projection explode.
    rng = np.random.default_rng(1)
    trace = rng.uniform(3.0, 5.0, 200)
    assert fit_calibration(trace, np.full(200, 3.5)) == FALLBACK_CALIBRATION


def test_non_numeric_values_are_dropped_not_fatal():
    trace = list(np.linspace(3.0, 5.0, 60)) + [None, "n/a"]
    rmp = list(1.9 * np.linspace(3.0, 5.0, 60) - 4.71) + [4.0, 4.0]
    slope, _ = fit_calibration(trace, rmp)
    assert slope == pytest.approx(1.9, abs=1e-6)


# ── projecting RMP onto the TRACE scale ─────────────────────────────────────

def test_calibration_raises_a_typical_rmp_score():
    # RMP runs ~0.8 lower than TRACE; 4.0 RMP is a strong professor, not a weak one.
    assert float(calibrate_rmp(4.0, CAL)) == pytest.approx(4.584, abs=0.01)


def test_calibration_is_the_inverse_of_the_fit():
    slope, intercept = CAL
    trace = 4.3
    assert float(calibrate_rmp(slope * trace + intercept, CAL)) == pytest.approx(trace)


def test_calibration_clips_to_the_scale():
    # RMP's wider spread projects the extremes past both ends of 1-5.
    assert float(calibrate_rmp(1.0, CAL)) >= 1.0
    assert float(calibrate_rmp(5.0, CAL)) <= 5.0


# ── per-response variance ───────────────────────────────────────────────────

def test_trace_variance_from_a_count_histogram():
    # One section, one 1-star and one 5-star: mean 3, ss 8, dof 1.
    assert trace_response_variance([[1, 0, 0, 0, 1]]) == pytest.approx(8.0)


def test_trace_variance_ignores_between_section_differences():
    # Two sections with no internal spread at all -> no response noise, even
    # though the two sections disagree completely.
    assert trace_response_variance([[5, 0, 0, 0, 0], [0, 0, 0, 0, 5]]) is None


def test_trace_variance_skips_single_response_sections():
    counts = [[1, 0, 0, 0, 0], [1, 0, 0, 0, 1]]
    assert trace_response_variance(counts) == pytest.approx(8.0)


def test_trace_variance_rejects_the_wrong_shape():
    assert trace_response_variance([[1, 2, 3]]) is None
    assert trace_response_variance([]) is None


def test_rmp_variance_pools_within_professor():
    # Professor A: 1 and 5 -> ss 8. dof = 2 rows - 1 professor = 1.
    assert rmp_response_variance([1, 5], ["a", "a"]) == pytest.approx(8.0)


def test_rmp_variance_ignores_between_professor_differences():
    # Each professor perfectly consistent -> zero response noise.
    assert rmp_response_variance([1, 1, 5, 5], ["a", "a", "b", "b"]) is None


def test_rmp_variance_drops_professors_with_one_review():
    assert rmp_response_variance([3, 1, 5], ["solo", "a", "a"]) == pytest.approx(8.0)


def test_rmp_variance_drops_out_of_range_and_missing_scores():
    assert rmp_response_variance([1, 5, 0, None, 99], ["a", "a", "a", "a", "a"]) \
        == pytest.approx(8.0)


def test_rmp_variance_is_none_when_nothing_usable():
    assert rmp_response_variance([], []) is None


# ── the pooled blend ────────────────────────────────────────────────────────

def test_the_regression_case_one_rmp_review_no_longer_wins():
    # Eno Ebong: 1 RMP review at 1.0 against 300 TRACE responses at 4.62.
    # The old 50/50 rule displayed 2.81.
    blended = float(blend_ratings(1.0, 1, 4.62, 300, CAL, VARS))
    assert blended > 4.5, "300 responses must outweigh one review"
    assert abs(blended - 2.81) > 1.5, "must not resemble the 50/50 answer"


def test_a_thin_trace_sample_cannot_dominate_either():
    # Symmetry check: the rule is about evidence, not about favouring TRACE.
    blended = float(blend_ratings(4.5, 400, 3.0, 2, CAL, VARS))
    assert blended > 4.0


def test_result_sits_between_the_two_calibrated_inputs():
    rmp_cal = float(calibrate_rmp(3.5, CAL))
    blended = float(blend_ratings(3.5, 50, 4.6, 50, CAL, VARS))
    assert min(rmp_cal, 4.6) <= blended <= max(rmp_cal, 4.6)


def test_more_evidence_moves_the_result_toward_that_source():
    light = float(blend_ratings(3.0, 5, 4.6, 200, CAL, VARS))
    heavy = float(blend_ratings(3.0, 500, 4.6, 200, CAL, VARS))
    assert heavy < light, "more RMP evidence must pull toward the RMP view"


def test_agreeing_sources_are_left_alone():
    # Both sources saying the same thing (on their own scales) must not shift it.
    trace = 4.4
    rmp_equivalent = CAL[0] * trace + CAL[1]
    assert float(blend_ratings(rmp_equivalent, 40, trace, 200, CAL, VARS)) \
        == pytest.approx(trace, abs=1e-6)


def test_reduces_to_a_plain_average_under_neutral_settings():
    # Identity calibration + equal variance + equal n is the only case where the
    # old 50/50 rule was correct, and the new one must agree there.
    assert float(blend_ratings(4.0, 10, 5.0, 10, (1.0, 0.0), (1.0, 1.0))) \
        == pytest.approx(4.5)


def _implied_weight_ratio(rmp, n_rmp, trace, n_trace, cal, variances):
    """Recover w_rmp / w_trace from a blend result, for weighting assertions."""
    cal_rmp = float(calibrate_rmp(rmp, cal))
    blended = float(blend_ratings(rmp, n_rmp, trace, n_trace, cal, variances))
    return (trace - blended) / (blended - cal_rmp)


def test_weights_compare_the_two_sources_on_the_same_scale():
    # The units bug: var_rmp is measured in RMP units, but it weights a value
    # calibrate_rmp already divided by the slope, so it needs the same slope^2
    # conversion. Without it RMP is under-weighted by slope^2 (~3.6x) and the
    # blend quietly degenerates into "TRACE, plus a rounding error".
    slope, _ = CAL
    var_rmp, var_trace = VARS
    ratio = _implied_weight_ratio(3.0, 100, 4.6, 100, CAL, VARS)
    expected = (100 * slope ** 2 / var_rmp) / (100 / var_trace)
    assert ratio == pytest.approx(expected, rel=1e-9)


def test_a_genuinely_noisier_source_is_down_weighted():
    # Same n on both sides, and now RMP really is noisier once both variances are
    # expressed on the TRACE scale (var_rmp / slope^2 = 4.0 against TRACE's 0.5).
    slope, _ = CAL
    noisy = (4.0 * slope ** 2, 0.5)
    trace = 4.6
    rmp_cal = float(calibrate_rmp(3.0, CAL))
    blended = float(blend_ratings(3.0, 100, trace, 100, CAL, noisy))
    assert abs(blended - trace) < abs(blended - rmp_cal)


def test_rmp_is_not_muted_at_realistic_sample_sizes():
    # Guards the regression directly: with the real measured constants, a
    # professor with substantial RMP evidence must actually move the number.
    # Under the old w = n / sigma^2 this shifted by less than 0.05.
    trace = 4.60
    blended = float(blend_ratings(3.0, 60, trace, 120, CAL, VARS))
    assert abs(blended - trace) > 0.15


def test_stays_inside_the_rating_scale():
    for rmp, n_rmp, trace, n_trace in [(1.0, 1, 1.0, 1), (5.0, 900, 5.0, 900),
                                       (1.0, 500, 5.0, 1), (5.0, 1, 1.0, 500)]:
        blended = float(blend_ratings(rmp, n_rmp, trace, n_trace, CAL, VARS))
        assert 1.0 <= blended <= 5.0


def test_monotonic_in_the_trace_score():
    scores = [float(blend_ratings(3.5, 20, t, 150, CAL, VARS))
              for t in (3.5, 4.0, 4.5, 5.0)]
    assert scores == sorted(scores)


def test_vectorised_and_scalar_agree():
    rmp, n_rmp = [1.0, 4.5, 3.0], [1, 400, 40]
    trace, n_trace = [4.62, 3.0, 4.4], [300, 2, 200]
    vector = blend_ratings(rmp, n_rmp, trace, n_trace, CAL, VARS)
    scalars = [float(blend_ratings(*args, CAL, VARS))
               for args in zip(rmp, n_rmp, trace, n_trace)]
    assert list(np.round(vector, 10)) == pytest.approx(scalars)


# ── the catalog wiring ──────────────────────────────────────────────────────

def _profs(rows):
    """A minimal rmp_profs frame: (rating, num_ratings, trace_overall, trace_reviews)."""
    return pd.DataFrame(rows, columns=["rating", "num_ratings",
                                       "trace_overall", "trace_reviews"])


def test_two_source_professor_is_blended():
    df = _profs([(1.0, 1, 4.62, 300)])
    assert apply_blended_rating(df, CAL, VARS) == 1
    assert df.at[0, "avg_rating"] > 4.5


def test_rmp_only_professor_is_put_on_the_trace_scale():
    """avg_rating is one column, so everyone in it has to be on one scale.

    Calibration is a unit conversion, not an evidence-weighted estimate: it needs
    no second source, any more than converting F to C needs a second thermometer.
    Leaving RMP-only professors raw left them ~0.8 low against the TRACE-only and
    two-source professors they are sorted and compared against.
    """
    df = _profs([(3.10, 40, np.nan, 0)])
    apply_blended_rating(df, CAL, VARS)
    assert df.at[0, "avg_rating"] == pytest.approx(float(calibrate_rmp(3.10, CAL)), abs=0.005)


def test_equally_good_professors_display_alike_whatever_their_source():
    """The property the scale fix exists for: an RMP-only professor and a
    TRACE-only professor of the same standing must show the same number."""
    trace_equivalent = float(calibrate_rmp(3.10, CAL))
    df = _profs([(3.10, 40, np.nan, 0),                  # RMP only
                 (0.0, 0, trace_equivalent, 210)])       # TRACE only, same standing
    apply_blended_rating(df, CAL, VARS)
    assert df.at[0, "avg_rating"] == pytest.approx(df.at[1, "avg_rating"], abs=0.01)


def test_calibrating_single_source_does_not_count_as_blending():
    """The return value reports professors with *two* sources pooled. A one-sided
    unit conversion is not a pooling and must not inflate that number."""
    df = _profs([(3.10, 40, np.nan, 0), (1.0, 1, 4.62, 300)])
    assert apply_blended_rating(df, CAL, VARS) == 1


def test_rmp_only_score_stays_inside_the_scale():
    # RMP's spread projects past both ends; the displayed column is still 1-5.
    df = _profs([(1.0, 5, np.nan, 0), (5.0, 5, np.nan, 0)])
    apply_blended_rating(df, CAL, VARS)
    assert 1.0 <= df.at[0, "avg_rating"] <= 5.0
    assert 1.0 <= df.at[1, "avg_rating"] <= 5.0


def test_trace_only_professor_keeps_the_raw_trace_score():
    df = _profs([(0.0, 0, 4.37, 210)])
    apply_blended_rating(df, CAL, VARS)
    assert df.at[0, "avg_rating"] == pytest.approx(4.37)


def test_a_trace_score_with_no_responses_is_not_treated_as_a_source():
    # trace_overall present but zero responses behind it -> RMP only.
    df = _profs([(3.10, 40, 4.90, 0)])
    assert apply_blended_rating(df, CAL, VARS) == 0
    assert df.at[0, "avg_rating"] == pytest.approx(float(calibrate_rmp(3.10, CAL)), abs=0.005)


def test_unrated_professor_gets_no_rating():
    # Stays missing (the catalog insert maps it to SQL NULL), and in particular
    # is not silently blended into some mid-scale number.
    df = _profs([(0.0, 0, np.nan, 0)])
    apply_blended_rating(df, CAL, VARS)
    assert pd.isna(df.at[0, "avg_rating"])


def test_rows_are_not_mixed_up_when_only_some_are_blended():
    # The blend is computed on a subset and assigned back by label; a positional
    # assignment here would scramble which professor got which rating.
    df = _profs([(3.10, 40, np.nan, 0),      # RMP only
                 (1.0, 1, 4.62, 300),        # blended
                 (0.0, 0, 4.37, 210),        # TRACE only
                 (4.8, 200, 4.9, 400)])      # blended
    assert apply_blended_rating(df, CAL, VARS) == 2
    assert df.at[0, "avg_rating"] == pytest.approx(float(calibrate_rmp(3.10, CAL)), abs=0.005)
    assert df.at[2, "avg_rating"] == pytest.approx(4.37)
    assert df.at[1, "avg_rating"] > 4.5
    assert df.at[3, "avg_rating"] > 4.5


def test_non_default_index_is_handled():
    df = _profs([(3.10, 40, np.nan, 0), (1.0, 1, 4.62, 300)])
    df.index = [77, 5]
    apply_blended_rating(df, CAL, VARS)
    assert df.at[77, "avg_rating"] == pytest.approx(float(calibrate_rmp(3.10, CAL)), abs=0.005)
    assert df.at[5, "avg_rating"] > 4.5


def test_ratings_are_rounded_to_two_decimals():
    df = _profs([(3.7, 33, 4.41, 187)])
    apply_blended_rating(df, CAL, VARS)
    assert df.at[0, "avg_rating"] == round(df.at[0, "avg_rating"], 2)


def test_empty_catalog_does_not_blow_up():
    df = _profs([])
    assert apply_blended_rating(df, CAL, VARS) == 0


def test_measure_calibration_uses_only_well_evidenced_rows():
    # 60 well-evidenced professors on a clean 1.9x line, plus noisy thin rows
    # that would flatten the slope if they were included.
    trace = np.linspace(3.0, 5.0, 60)
    rows = [(1.9 * t - 4.71, CALIBRATION_MIN_RMP, t, CALIBRATION_MIN_TRACE) for t in trace]
    rows += [(2.0, 1, 4.9, 5), (5.0, 2, 3.2, 3)]
    slope, intercept = measure_calibration(_profs(rows))
    assert slope == pytest.approx(1.9, abs=0.01)
    assert intercept == pytest.approx(-4.71, abs=0.05)


def test_measure_calibration_falls_back_on_a_thin_corpus():
    assert measure_calibration(_profs([(4.0, 30, 4.5, 200)])) == FALLBACK_CALIBRATION


def test_measure_variances_falls_back_when_unmeasurable():
    assert measure_variances([], [], []) == FALLBACK_VARIANCES


def test_measure_variances_reports_measured_values():
    var_rmp, var_trace = measure_variances([1, 5], ["a", "a"], [[1, 0, 0, 0, 1]])
    assert (var_rmp, var_trace) == (pytest.approx(8.0), pytest.approx(8.0))


# ── the scalar twins, and the frontend copy ─────────────────────────────────
#
# blend_ratings is vectorised over numpy because precompute pools ~5,000
# professors at once. Two other places need the same arithmetic one professor at
# a time and cannot carry numpy: rating_scale.pool_ratings (pure Python), and
# frontend/src/utils/ratingBlend.ts, which pools a course-filtered subset in the
# browser. Three copies of a weighted mean is two too many to leave unpinned —
# the filtered card carried (rmp + trace) / 2 for exactly as long as nothing
# compared it to this file.

def test_pool_ratings_matches_the_vectorised_blend():
    """The scalar twin agrees with blend_ratings on the batch path's own inputs."""
    weight = rmp_weight_per_rating(CAL, VARS)
    cases = [(3.10, 5, 4.50, 300), (3.10, 400, 4.50, 10), (4.63, 88, 4.74, 512),
             (1.00, 3, 2.20, 40), (5.00, 250, 4.96, 250)]
    for rmp, n_rmp, trace, n_trace in cases:
        vectorised = float(blend_ratings(rmp, n_rmp, trace, n_trace, CAL, VARS))
        assert pool_ratings(rmp, n_rmp, trace, n_trace, CAL, weight) == pytest.approx(
            vectorised, abs=1e-9), f"scalar twin drifted on {(rmp, n_rmp, trace, n_trace)}"


def test_rmp_weight_per_rating_is_the_whole_of_the_pooling_weights():
    """One scalar reproduces the two-variance weighting, which is why only it ships.

    blend_ratings weighs RMP by n * slope^2 / var_rmp and TRACE by n / var_trace.
    A weighted mean is invariant to scaling both weights, so var_trace divides
    out and the browser needs one number instead of two variances measured from
    raw review rows it will never hold.
    """
    slope, _ = CAL
    var_rmp, var_trace = VARS
    assert rmp_weight_per_rating(CAL, VARS) == pytest.approx(
        (slope ** 2 / var_rmp) / (1.0 / var_trace))


def test_pool_ratings_single_sided_selections_stay_on_the_trace_scale():
    """A course selection can hold one source only; it still gets a comparable number.

    Matches apply_blended_rating's rule for a single-source professor: RMP alone
    is projected, not left raw. avg_rating is one column readers sort on, so a
    filtered card that returned raw RMP would show the same professor a point
    lower for having a course unchecked.
    """
    weight = rmp_weight_per_rating(CAL, VARS)
    assert pool_ratings(3.10, 5, None, 0, CAL, weight) == pytest.approx(
        float(calibrate_rmp(3.10, CAL)))
    assert pool_ratings(None, 0, 4.50, 300, CAL, weight) == 4.50
    # A rating with no responses behind it is not evidence on either side.
    assert pool_ratings(3.10, 0, 4.50, 0, CAL, weight) is None
    assert pool_ratings(None, 0, None, 0, CAL, weight) is None


def test_frontend_selftest_constants_are_this_module_s_output():
    """The values hardcoded in ratingBlend.selftest.ts, recomputed here.

    That file is a runnable check with no Python in it, so its expectations are
    constants — and a constant copied out of another language is exactly the kind
    of number that goes stale silently. If FALLBACK_CALIBRATION, FALLBACK_VARIANCES
    or the arithmetic moves, this fails and names the file to update.
    """
    weight = rmp_weight_per_rating(FALLBACK_CALIBRATION, FALLBACK_VARIANCES)
    expected = {
        "rmpWeightPerRating": weight,
        "project 3.10": project_rmp(3.10, FALLBACK_CALIBRATION),
        "project 5.00": project_rmp(5.00, FALLBACK_CALIBRATION),
        "project 1.00": project_rmp(1.00, FALLBACK_CALIBRATION),
        "pool 3.10/5 4.50/300": pool_ratings(3.10, 5, 4.50, 300, FALLBACK_CALIBRATION, weight),
        "pool 3.10/400 4.50/10": pool_ratings(3.10, 400, 4.50, 10, FALLBACK_CALIBRATION, weight),
        "pool 3.10/40 4.50/120": pool_ratings(3.10, 40, 4.50, 120, FALLBACK_CALIBRATION, weight),
    }
    # Rounded to the 6 places the selftest's `near` compares at (5e-4).
    assert {k: round(v, 6) for k, v in expected.items()} == {
        "rmpWeightPerRating": 1.839896,
        "project 3.10": 4.172269,
        "project 5.00": 4.970588,
        "project 1.00": 3.289916,
        "pool 3.10/5 4.50/300": 4.490249,
        "pool 3.10/400 4.50/10": 4.176662,
        "pool 3.10/40 4.50/120": 4.375412,
    }, "update frontend/src/utils/ratingBlend.selftest.ts to match"
