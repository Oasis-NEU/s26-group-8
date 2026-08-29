// Runnable self-check for ratingBlend (no unit-test framework is installed).
// Run: cd frontend && npx tsx src/utils/ratingBlend.selftest.ts
// Prints PASS/FAIL per check and exits non-zero on any failure.
//
// Every expected value below is the output of backend/rating_scale.py's
// project_rmp / pool_ratings on the same inputs. They are asserted from the
// Python side too, by
//   test_rating_blend.test_frontend_selftest_constants_are_this_module_s_output
// which recomputes them and names this file when they disagree — so a change to
// the calibration, the variances or the arithmetic fails there rather than
// leaving a stale constant here that quietly agrees with nothing.
import { argv } from 'node:process';
import { fileURLToPath } from 'node:url';
import { projectRmp, poolRatings } from './ratingBlend';
import type { RatingBlend } from './ratingBlend';

// The measured fallback calibration, and the weight scalar it implies with the
// measured fallback variances: 2.38^2 * 0.534 / 1.644.
const BLEND: RatingBlend = {
  slope: 2.38,
  intercept: -6.83,
  rmpWeightPerRating: 1.839896,
};

function selftest(): number {
  const fails: string[] = [];
  const near = (label: string, got: number | null, want: number | null) => {
    const ok = got === null || want === null
      ? got === want
      : Math.abs(got - want) < 5e-4;
    if (!ok) fails.push(`${label} (got ${got}, want ${want})`);
    console.log(`${ok ? 'PASS' : 'FAIL'}: ${label} -> ${got}`);
  };

  // ── projectRmp ──
  near('project 3.10', projectRmp(3.10, BLEND), 4.172269);
  near('project 5.00 clips below 5 (RMP is 2.4x wider)', projectRmp(5.00, BLEND), 4.970588);
  near('project 1.00 clips at the floor', projectRmp(1.00, BLEND), 3.289916);

  // ── poolRatings, two sources ──
  // The bug this module replaced: (3.10 + 4.50) / 2 = 3.80, on neither scale.
  near('pool 3.10/5 with 4.50/300 leans TRACE',
    poolRatings(3.10, 5, 4.50, 300, BLEND), 4.490249);
  near('pool 3.10/400 with 4.50/10 leans RMP',
    poolRatings(3.10, 400, 4.50, 10, BLEND), 4.176662);
  near('pool of two equal projected values is that value',
    poolRatings(3.10, 50, 4.172269, 50, BLEND), 4.172269);

  // ── single-sided selections ──
  near('RMP-only selection projects rather than returning raw RMP',
    poolRatings(3.10, 5, null, 0, BLEND), 4.172269);
  near('TRACE-only selection is already on the scale',
    poolRatings(null, 0, 4.50, 300, BLEND), 4.50);
  near('a rating with zero responses behind it is not evidence',
    poolRatings(3.10, 0, 4.50, 0, BLEND), null);
  near('empty selection', poolRatings(null, 0, null, 0, BLEND), null);

  // ── missing parameters ──
  // Serving no ratingBlend must never fall back to a raw average, which is the
  // defect the module exists to remove.
  near('no blend params, TRACE present -> TRACE', poolRatings(3.10, 5, 4.50, 300, null), 4.50);
  near('no blend params, RMP only -> nothing projectable', poolRatings(3.10, 5, null, 0, null), null);

  // ── the pool cannot leave the interval its inputs span ──
  const pooled = poolRatings(3.10, 40, 4.50, 120, BLEND)!;
  const lo = Math.min(projectRmp(3.10, BLEND), 4.50);
  const hi = Math.max(projectRmp(3.10, BLEND), 4.50);
  const inside = pooled >= lo && pooled <= hi;
  if (!inside) fails.push('pooled value outside [projected RMP, TRACE]');
  console.log(`${inside ? 'PASS' : 'FAIL'}: pooled ${pooled.toFixed(4)} lies in [${lo.toFixed(4)}, ${hi.toFixed(4)}]`);

  if (fails.length) {
    console.error(`\n${fails.length} FAILED:\n  ${fails.join('\n  ')}`);
    return 1;
  }
  console.log('\nAll checks passed.');
  return 0;
}

if (argv[1] && fileURLToPath(import.meta.url) === argv[1]) {
  process.exit(selftest());
}

export { selftest };
