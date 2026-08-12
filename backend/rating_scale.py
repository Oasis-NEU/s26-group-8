"""The RMP -> TRACE scale conversion, in pure Python.

RMP and TRACE do not measure on the same scale: measured on this corpus RMP runs
about 0.8 lower and 2.4x wider, so `avg_rating` projects RMP onto TRACE before
the two are ever pooled or compared. That projection is fitted in precompute and
then needed again in server.py, to show a reader the number that actually went
into the blend.

It lives here, once, rather than in both. server.py carries no pandas or numpy on
purpose (see its module docstring), so it cannot import precompute; the
alternative was a second copy of a fitted statistic in the one place that has to
agree with the first, which is the same class of defect this module exists to
fix. The leaderboard tooltip displayed raw RMP beside a blend computed from
projected RMP, and no arithmetic a reader tried could reconcile them.

precompute keeps the pandas coercion and the vectorised clip; only the fit's
arithmetic and every threshold moved here.
"""

import math

# rmp ~ slope * trace + intercept. Used when the fit cannot be trusted; measured
# on an earlier corpus, so it is a plausible mapping rather than a neutral one —
# there is no neutral choice, since identity would assert the scales match.
FALLBACK_CALIBRATION = (2.38, -6.83)

# Per-response variance: (RMP, TRACE). Measured on each source's own scale, so
# they are not comparable as a ratio until slope^2 converts RMP's — see
# rmp_weight_per_rating. Used when precompute could not measure them, and as the
# fallback server.py serves when the rating_meta row is missing.
FALLBACK_VARIANCES = (1.644, 0.534)

# What counts as well-evidenced enough to inform the fit. Thin samples on either
# side are mostly noise, and including them flattens the slope toward zero, which
# understates how much wider the RMP scale is.
CALIBRATION_MIN_RMP = 20
CALIBRATION_MIN_TRACE = 100

CALIBRATION_MIN_POINTS = 30   # too few pairs -> keep the fallback fit
CALIBRATION_MIN_SLOPE = 0.5   # a flat slope makes the inverse projection explode
CALIBRATION_MIN_CORR = 0.1    # unrelated (or inverted) scales -> no fit

RATING_MIN, RATING_MAX = 1.0, 5.0


def fit_rma(trace_ratings, rmp_ratings):
    """Fit `rmp ~ slope * trace + intercept`; returns (slope, intercept) or None.

    None means "do not trust this fit" and leaves the fallback decision to the
    caller, which is what lets precompute log why it fell back while server.py
    stays quiet about it.

    Slope is the ratio of standard deviations (reduced major axis), not the OLS
    coefficient, because this fit exists to be *inverted*. OLS minimises error in
    rmp given trace, so inverting it over-disperses — it stretches the projected
    values by 1/corr, measured 1.42x wider than TRACE's own spread. Matching the
    two spreads is what "project onto the TRACE scale" has to mean for the
    inverse-variance weights downstream to be in the same units.

    RMA takes its sign from the correlation, so unlike OLS it cannot notice an
    inverted relationship on its own — hence the explicit CALIBRATION_MIN_CORR
    guard, which also catches the zero-variance case where corr is undefined.

    Inputs must already be numeric and finite; precompute coerces with pandas
    before calling, and server.py reads FLOAT columns.
    """
    x = list(trace_ratings)
    y = list(rmp_ratings)
    n = len(x)
    if n != len(y) or n < CALIBRATION_MIN_POINTS:
        return None
    if max(x) == min(x) or max(y) == min(y):
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((v - mx) ** 2 for v in x)
    syy = sum((v - my) ** 2 for v in y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    if sxx <= 0 or syy <= 0:
        return None
    corr = sxy / math.sqrt(sxx * syy)
    if not math.isfinite(corr) or corr < CALIBRATION_MIN_CORR:
        return None
    # sd(y)/sd(x); the (n-1) in each cancels, so it never appears.
    slope = math.sqrt(syy / sxx)
    intercept = my - slope * mx
    if not math.isfinite(slope) or not math.isfinite(intercept):
        return None
    if slope <= CALIBRATION_MIN_SLOPE:
        return None
    return slope, intercept


def project_rmp(rmp_rating, calibration):
    """Put one RMP rating on the TRACE scale, clipped to the 1-5 range.

    Inverse of the fit, which predicts RMP *from* TRACE. Clipping matters because
    RMP's wider spread projects the extremes past the ends of the scale — and it
    is why a professor at RMP 5.00 does not land on 5.00 here.

    The scalar twin of precompute.calibrate_rmp, which stays vectorised over
    numpy for the batch path. test_rating_calibration pins the two together.
    """
    if rmp_rating is None:
        return None
    slope, intercept = calibration
    return min(max((float(rmp_rating) - intercept) / slope, RATING_MIN), RATING_MAX)


def rmp_weight_per_rating(calibration, variances):
    """How much one RMP rating weighs against one TRACE response. ~1.88 measured.

    The whole of what an inverse-variance pool needs beyond the two means and
    their two counts. `w_rmp = n_rmp * slope^2 / var_rmp` and `w_trace = n_trace
    / var_trace` (precompute.blend_ratings), and a weighted mean is unchanged by
    scaling both weights, so multiplying through by var_trace leaves one number:
    slope^2 * var_trace / var_rmp.

    Reducing it to a scalar is what lets the professor page pool a course-filtered
    subset without shipping the per-response variances, which are measured from
    raw review rows no client has and no served table carries.
    """
    slope, _ = calibration
    var_rmp, var_trace = variances
    return slope ** 2 * var_trace / var_rmp


def pool_ratings(rmp_rating, n_rmp, trace_rating, n_trace, calibration, weight):
    """Inverse-variance pool of one professor's two sources, on the TRACE scale.

    `weight` is rmp_weight_per_rating's scalar. The scalar twin of
    precompute.blend_ratings, which stays vectorised over numpy for the batch
    path — test_rating_blend pins the two together, and frontend/src/lib/
    ratingBlend.ts mirrors this function for the course-filtered card.

    Either side may be None (a course selection can hold RMP ratings and no TRACE
    responses, or the reverse), in which case the other source is returned on the
    TRACE scale — projected, for RMP, matching what apply_blended_rating writes
    for a single-source professor. Returns None when neither side has evidence.
    """
    has_rmp = rmp_rating is not None and n_rmp > 0
    has_trace = trace_rating is not None and n_trace > 0
    if not has_rmp and not has_trace:
        return None
    if not has_trace:
        return project_rmp(rmp_rating, calibration)
    if not has_rmp:
        return float(trace_rating)
    w_rmp = n_rmp * weight
    return ((w_rmp * project_rmp(rmp_rating, calibration) + n_trace * float(trace_rating))
            / (w_rmp + n_trace))
