// The RMP -> TRACE projection and the two-source pool, for a course-filtered
// subset of one professor's ratings.
//
// The backend writes `avg_rating` for the professor as a whole (see
// precompute.apply_blended_rating). The professor page lets a reader filter to a
// course selection, and the card then has to answer with the same rule for a
// subset the catalog has no column for. It used to answer `(rmp + trace) / 2` —
// the exact rule the blend replaced, so unchecking one course moved the headline
// number by up to half a point onto a scale neither source is on.
//
// This is the scalar twin of backend/rating_scale.py's project_rmp and
// pool_ratings, kept honest by ratingBlend.selftest.ts, whose expected values
// come from that module. The parameters are served, not hardcoded: `ratingBlend`
// on the professor payload carries the calibration and the one weight scalar, so
// a re-scrape that shifts either scale shifts this too. What is duplicated here
// is a weighted mean, not the variance machinery behind the weight — see
// rating_scale.rmp_weight_per_rating.

export interface RatingBlend {
  slope: number;
  intercept: number;
  rmpWeightPerRating: number;
}

const RATING_MIN = 1.0;
const RATING_MAX = 5.0;

/** Put one RMP rating on the TRACE scale, clipped to the 1-5 range.
 *
 * Inverse of a fit that predicts RMP *from* TRACE. Clipping is why a professor
 * at RMP 5.00 does not land on 5.00: RMP's spread is ~2.4x wider, so its
 * extremes project past the ends of the scale.
 */
export function projectRmp(rmpRating: number, blend: RatingBlend): number {
  const projected = (rmpRating - blend.intercept) / blend.slope;
  return Math.min(Math.max(projected, RATING_MIN), RATING_MAX);
}

/** Inverse-variance pool of a selection's two sources, on the TRACE scale.
 *
 * Either side may be absent — a course selection can hold RMP ratings and no
 * TRACE responses, or the reverse — in which case the other source is returned
 * on the TRACE scale, projected for RMP. That matches what the backend writes
 * for a single-source professor, and it is why an RMP-only selection does not
 * show its raw RMP mean: `avg_rating` is one number readers sort and compare
 * professors by, so every value in it has to mean the same thing.
 *
 * Returns null when the selection has no evidence on either side.
 */
export function poolRatings(
  rmpRating: number | null,
  nRmp: number,
  traceRating: number | null,
  nTrace: number,
  blend: RatingBlend | null,
): number | null {
  const hasRmp = rmpRating !== null && nRmp > 0;
  const hasTrace = traceRating !== null && nTrace > 0;
  if (!hasRmp && !hasTrace) return null;
  // Without the served parameters there is no projection and so no common scale.
  // Falling back to a raw average here would reintroduce exactly the bug this
  // module exists to fix, so the TRACE side — already on the target scale — is
  // the only answer left, and an RMP-only selection has none.
  if (!blend) return hasTrace ? traceRating : null;
  if (!hasTrace) return projectRmp(rmpRating as number, blend);
  if (!hasRmp) return traceRating as number;
  const wRmp = nRmp * blend.rmpWeightPerRating;
  return (wRmp * projectRmp(rmpRating as number, blend) + nTrace * (traceRating as number))
    / (wRmp + nTrace);
}
