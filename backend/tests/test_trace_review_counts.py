"""trace_reviews must count the ratings behind the rating, not survey submitters.

The board's "Ratings" column, the BOARD_MIN_REVIEWS floor and the shrinkage
weight all read professors_catalog.total_reviews = num_ratings + trace_reviews.
trace_reviews used to sum `completed` off one arbitrary question row per section:

  - `completed` counts students who submitted the section's survey, not students
    who answered the overall question the rating is computed from;
  - it is not constant across a section's question rows (5,554 of 55,049 sections
    on the 2026-08-03 corpus carry more than one value, some rows reporting 0),
    so the total moved with whichever row `drop_duplicates` happened to keep;
  - sections whose survey form has no overall item counted in full — 79 of Susan
    Sieloff's 83, so the board claimed 338 ratings behind a rating measured from
    21 responses.

Now it sums total_responses over the same rows the mean is weighted by, so
`trace_rating` and `trace_reviews` are one measurement. Same pairing
test_num_ratings_count.py enforces on the RMP side.

No database: driven by synthetic frames.
"""

import pandas as pd

from precompute import trace_review_counts


def overall_rows(rows):
    """The `merged` frame: overall-question rows joined to a name_key. Only the
    columns the count reads; the real frame carries mean/course_id/term_id too."""
    return pd.DataFrame(
        [{"name_key": nk, "total_responses": n} for nk, n in rows]
    )


# ── the quantity ────────────────────────────────────────────────────────────

def test_counts_responses_to_the_overall_question():
    counts = trace_review_counts(overall_rows([("a prof", 23), ("a prof", 19)]))
    assert counts == {"a prof": 42}


def test_each_instructor_is_counted_separately():
    counts = trace_review_counts(
        overall_rows([("a prof", 10), ("b prof", 4), ("a prof", 6)]))
    assert counts == {"a prof": 16, "b prof": 4}


def test_a_section_nobody_answered_adds_nothing():
    # The old count would have added the section's `completed` here.
    counts = trace_review_counts(overall_rows([("a prof", 12), ("a prof", 0)]))
    assert counts == {"a prof": 12}


def test_sections_with_no_overall_question_never_reach_the_count():
    # Sieloff's 79: they are absent from `merged` by construction, because the
    # count now reads the rating's own rows rather than every section's survey.
    counts = trace_review_counts(overall_rows([("susan sieloff", 21)]))
    assert counts == {"susan sieloff": 21}


# ── shape the pipeline depends on ───────────────────────────────────────────

def test_counts_are_ints():
    # A float slips through fillna and lands in an INT column via .astype(int);
    # the board also compares it against BOARD_MIN_REVIEWS.
    counts = trace_review_counts(overall_rows([("a prof", 7.0)]))
    assert isinstance(counts["a prof"], int)
    assert counts["a prof"] == 7


def test_missing_response_counts_are_zero_not_nan():
    # NaN would propagate through the sum and make the whole professor's count
    # NaN, which fillna(0) downstream then reads as a professor with no TRACE.
    counts = trace_review_counts(
        overall_rows([("a prof", float("nan")), ("a prof", 5)]))
    assert counts == {"a prof": 5}


def test_an_empty_frame_yields_no_counts():
    # A corpus with no overall questions at all: every professor falls back to 0.
    assert trace_review_counts(overall_rows([])) == {}


# ── wiring ──────────────────────────────────────────────────────────────────

def test_precompute_counts_from_the_frame_the_rating_is_averaged_from():
    import inspect

    import precompute

    body = inspect.getsource(precompute.main)
    assert "trace_reviews_lookup = trace_review_counts(merged)" in body, (
        "the count must read `merged` — the overall rows trace_overall is the "
        "weighted mean of — so the rating and its n describe one set of rows")
    average = body.index("trace_avg = merged.groupby")
    count = body.index("trace_review_counts(merged)")
    assert average < count
