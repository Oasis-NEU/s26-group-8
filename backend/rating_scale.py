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
