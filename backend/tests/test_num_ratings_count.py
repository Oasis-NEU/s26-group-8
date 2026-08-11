"""num_ratings must count the ratings we hold, not the ones RMP claims.

RMP's own numRatings counter disagrees with the rating nodes RMP serves for 392
professors: 376 low (usually by 1-3, e.g. "juner zhu" claims 5 and serves 8) and
16 high, including 5 who claim a rating and serve none at all. The counter is a
denormalised aggregate RMP does not recalculate when a rating is added or removed.

Every count downstream is derived from num_ratings — total_reviews, the GOATED
review floor, the leaderboard's shrinkage weight, and n_rmp in the rating blend —
so the displayed number disagreed with the reviews actually listed on the page.
Counting the rows we store makes the count and the cards the same measurement.

No database: driven by synthetic frames.
"""

import pandas as pd
import pytest

from precompute import apply_counted_num_ratings, apply_counted_rmp_rating


def profs(rows):
    """An rmp_profs frame at the point apply_counted_num_ratings sees it —
    post-merge_rmp_aliases, so _name_key exists and is unique."""
    return pd.DataFrame(
        [{"_name_key": nk, "num_ratings": nr, "rating": 4.0} for nk, nr in rows]
    )


# ── the mismatch this exists to fix ─────────────────────────────────────────

def test_counter_lower_than_rows_served_is_raised():
    # "juner zhu": RMP claims 5, served 8.
    df = profs([("juner zhu", 5)])
    apply_counted_num_ratings(df, ["juner zhu"] * 8)
    assert df.at[0, "num_ratings"] == 8


def test_counter_higher_than_rows_served_is_lowered():
    df = profs([("a prof", 40)])
    apply_counted_num_ratings(df, ["a prof"] * 37)
    assert df.at[0, "num_ratings"] == 37


def test_claimed_rating_with_no_rows_becomes_zero():
    # The 5 real professors whose summary claims a rating RMP never serves.
    df = profs([("beth cohen", 1)])
    apply_counted_num_ratings(df, ["someone else"])
    assert df.at[0, "num_ratings"] == 0


def test_matching_counter_is_left_alone():
    df = profs([("agrees", 3)])
    apply_counted_num_ratings(df, ["agrees"] * 3)
    assert df.at[0, "num_ratings"] == 3


def test_reviews_are_counted_under_the_merged_alias_key():
    # merge_rmp_aliases folds RMP's duplicate profile pages onto one _name_key;
    # the reviews from every one of those pages carry that same key.
    df = profs([("aaron daniels", 23)])
    apply_counted_num_ratings(df, ["aaron daniels"] * 23)
    assert df.at[0, "num_ratings"] == 23


# ── it must not disturb anything else ───────────────────────────────────────

def test_the_alias_weighted_rating_is_untouched():
    # merge_rmp_aliases already used RMP's counters to weight `rating` across
    # duplicate profiles. Recounting must not reach back and change that.
    df = profs([("a prof", 5)])
    df.at[0, "rating"] = 4.37
    apply_counted_num_ratings(df, ["a prof"] * 9)
    assert df.at[0, "rating"] == 4.37


def test_num_ratings_stays_an_integer_column():
    # total_reviews does num_ratings.astype(int) right after this runs.
    df = profs([("a prof", 5), ("no rows", 2)])
    apply_counted_num_ratings(df, ["a prof"] * 9)
    assert str(df["num_ratings"].dtype).startswith("int")


def test_review_keys_with_no_professor_row_are_ignored():
    df = profs([("known", 1)])
    apply_counted_num_ratings(df, ["known", "never scraped", "never scraped"])
    assert len(df) == 1
    assert df.at[0, "num_ratings"] == 1


def test_a_null_counter_counts_as_zero_not_nan():
    df = profs([("missing counter", None)])
    apply_counted_num_ratings(df, ["missing counter"] * 4)
    assert df.at[0, "num_ratings"] == 4


def test_empty_review_set_zeroes_every_professor():
    df = profs([("a", 5), ("b", 9)])
    apply_counted_num_ratings(df, [])
    assert list(df["num_ratings"]) == [0, 0]


# ── the count it reports ────────────────────────────────────────────────────

def test_returns_how_many_professors_were_corrected():
    df = profs([("low", 5), ("high", 40), ("agrees", 2)])
    changed = apply_counted_num_ratings(
        df, ["low"] * 8 + ["high"] * 37 + ["agrees"] * 2)
    assert changed == 2


def test_reports_zero_when_every_counter_already_agrees():
    df = profs([("a", 1), ("b", 2)])
    assert apply_counted_num_ratings(df, ["a", "b", "b"]) == 0


def test_an_empty_professor_frame_is_handled():
    df = profs([])
    assert apply_counted_num_ratings(df, ["nobody"]) == 0


# ── the mean must describe the same rows as the count ───────────────────────
#
# Recounting num_ratings while leaving `rating` as RMP's stale average left the
# two describing different populations, and the blend consumes both: `rating` as
# the RMP measurement, num_ratings as its precision. The quality values are
# already in hand for the variance measurement, so recompute the mean from them.

def test_the_mean_is_recomputed_from_the_ratings_we_hold():
    df = profs([("a prof", 3)])
    df.at[0, "rating"] = 4.9   # RMP's stale average
    apply_counted_rmp_rating(df, ["a prof"] * 3, [3.0, 4.0, 5.0])
    assert df.at[0, "rating"] == pytest.approx(4.0)


def test_a_professor_with_no_stored_ratings_keeps_rmps_number():
    # Nothing to average, so there is no better measurement to substitute.
    # has_rmp_data gates on num_ratings > 0, so this professor is RMP-less anyway.
    df = profs([("beth cohen", 1)])
    df.at[0, "rating"] = 4.5
    apply_counted_rmp_rating(df, ["someone else"], [5.0])
    assert df.at[0, "rating"] == pytest.approx(4.5)


def test_out_of_range_quality_values_are_ignored():
    # A 0 or a null in the export is a missing rating, not a rating of zero.
    df = profs([("a prof", 2)])
    apply_counted_rmp_rating(df, ["a prof"] * 3, [4.0, 5.0, 0.0])
    assert df.at[0, "rating"] == pytest.approx(4.5)


def test_a_professor_whose_every_rating_is_unusable_keeps_rmps_number():
    df = profs([("a prof", 2)])
    df.at[0, "rating"] = 3.3
    apply_counted_rmp_rating(df, ["a prof"] * 2, [None, 0.0])
    assert df.at[0, "rating"] == pytest.approx(3.3)


def test_the_mean_and_the_count_agree_after_both_run():
    """The point of the pair: `rating` and num_ratings describe one set of rows.

    Out-of-range values are dropped from the mean but still counted as ratings —
    RMP served the rating node, it just carries no usable quality score.
    """
    df = profs([("a prof", 99)])
    apply_counted_num_ratings(df, ["a prof"] * 3)
    apply_counted_rmp_rating(df, ["a prof"] * 3, [3.0, 4.0, 5.0])
    assert df.at[0, "num_ratings"] == 3
    assert df.at[0, "rating"] == pytest.approx(4.0)


def test_returns_how_many_means_moved():
    df = profs([("moved", 1), ("agrees", 1)])
    df.at[0, "rating"] = 2.0
    df.at[1, "rating"] = 4.0
    changed = apply_counted_rmp_rating(df, ["moved", "agrees"], [5.0, 4.0])
    assert changed == 1


def test_a_professor_who_never_had_a_rating_is_not_reported_as_corrected():
    # `NaN != NaN` is True in pandas, so a professor with no rating before and
    # none after counts as a correction unless the comparison says otherwise.
    df = profs([("no rating anywhere", 0)])
    df.at[0, "rating"] = float("nan")
    assert apply_counted_rmp_rating(df, ["someone else"], [4.0]) == 0


def test_an_empty_professor_frame_is_handled_by_the_mean_pass():
    assert apply_counted_rmp_rating(profs([]), ["nobody"], [4.0]) == 0


# ── wiring ──────────────────────────────────────────────────────────────────

def test_precompute_recounts_before_deriving_total_reviews():
    import inspect

    import precompute

    body = inspect.getsource(precompute.main)
    recount = body.index("apply_counted_num_ratings(")
    derive = body.index('rmp_profs["total_reviews"] =')
    assert recount < derive, "total_reviews must be built from the recounted number"


def test_precompute_recomputes_the_mean_before_it_is_calibrated():
    # measure_calibration fits on `rating`, so the fit has to see the recomputed
    # mean, not RMP's stale one.
    import inspect

    import precompute

    body = inspect.getsource(precompute.main)
    remean = body.index("apply_counted_rmp_rating(")
    calibrate = body.index("measure_calibration(")
    assert remean < calibrate, "the fit must run on the recomputed mean"
