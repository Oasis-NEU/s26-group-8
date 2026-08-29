"""A review fetch that stops on a failed page must be reported as incomplete.

precompute no longer trusts RMP's `numRatings` counter — it counts the rating
rows we actually hold and remeans `rating` over the same rows
(apply_counted_num_ratings / apply_counted_rmp_rating), because that counter is a
stale denormalised aggregate that disagreed with the nodes RMP served for 392
professors.

That makes a truncated fetch a data-corruption path rather than a cosmetic one.
A professor whose page 1 succeeded and page 2 failed keeps a plausible-looking
list, and the run publishes num_ratings, `rating`, total_reviews, the
BOARD_MIN_REVIEWS floor and the blend weight all computed from part of their
reviews. Nothing downstream can see it: scrape_guard's floors are corpus-wide
(98% of ~44.5k rows), so one professor losing 300 of 400 ratings is far inside
the noise.

The old retry pass keyed on `not p.reviews`, which is the wrong predicate in both
directions — it re-fetched legitimately zero-review professors every run, and
never retried the partial fetches that actually matter.

No network: RMPSchool is built without __init__ (which bootstraps a real session)
and _graphql_post is replaced with a scripted page sequence.
"""

import sys
from pathlib import Path

import pytest

# fetch_lite does `from models import ...`, a sibling import that resolves only
# with Better_Scraper itself on the path. Its own tests live beside it and do the
# same, but ci.yml runs `pytest tests` from backend/ and nothing else, so a test
# that has to run in CI lives here and brings the path with it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Better_Scraper"))

from fetch_lite import RMPSchool  # noqa: E402
from models import Professor      # noqa: E402


def _page(n_reviews, has_next, cursor="c"):
    """One GraphQL ratings page in the shape _parse_ratings reads."""
    return {
        "data": {
            "node": {
                "ratings": {
                    "edges": [
                        {"node": {"class": "CS2500", "qualityRating": 4,
                                  "difficultyRatingRounded": 3, "date": "2025-01-01",
                                  "comment": f"review {i}"}}
                        for i in range(n_reviews)
                    ],
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                }
            }
        }
    }


def _school(pages):
    """An RMPSchool whose _graphql_post replays `pages`, one per call.

    A page of None stands for a failed request — what _graphql_post returns once
    its own retries are exhausted.
    """
    school = object.__new__(RMPSchool)
    calls = {"n": 0}

    def fake_post(payload, retries=2):
        i = calls["n"]
        calls["n"] += 1
        return pages[i] if i < len(pages) else None

    school._graphql_post = fake_post
    school.calls = calls
    return school


def _prof(name="Ada Lovelace", num_ratings="400"):
    return Professor(name=name, graphql_id="Teacher-1", num_ratings=num_ratings)


# ── the flag itself ─────────────────────────────────────────────────────────

def test_a_clean_single_page_fetch_is_complete():
    school = _school([_page(5, has_next=False)])
    reviews, complete = school._fetch_reviews_for_professor(_prof())
    assert len(reviews) == 5
    assert complete is True


def test_pagination_to_the_end_is_complete():
    school = _school([_page(100, True), _page(100, True), _page(40, False)])
    reviews, complete = school._fetch_reviews_for_professor(_prof())
    assert len(reviews) == 240
    assert complete is True


def test_a_failed_second_page_is_incomplete():
    """The case the whole change exists for: 100 of 400 ratings, marked short."""
    school = _school([_page(100, True), None])
    reviews, complete = school._fetch_reviews_for_professor(_prof())
    assert len(reviews) == 100
    assert complete is False


def test_a_failed_first_page_is_incomplete():
    school = _school([None])
    reviews, complete = school._fetch_reviews_for_professor(_prof())
    assert reviews == []
    assert complete is False


def test_a_genuinely_empty_professor_is_complete_not_failed():
    """Zero reviews is an answer, not an error.

    RMP claims a rating count for professors it serves no rating nodes for. The
    old retry pass re-fetched every one of them on every run and reported them as
    failures; they are complete fetches that found nothing.
    """
    school = _school([_page(0, has_next=False)])
    reviews, complete = school._fetch_reviews_for_professor(_prof())
    assert reviews == []
    assert complete is True


def test_hitting_the_page_cap_is_complete_not_truncated():
    """A deliberate cap is not a truncation a retry could fix.

    MAX_REVIEW_PAGES stops the loop at the same place every time, so marking it
    incomplete would put a professor in the retry pass forever and report them as
    a failure on every run.
    """
    import fetch_lite
    pages = [_page(100, True)] * (fetch_lite.MAX_REVIEW_PAGES + 5)
    school = _school(pages)
    reviews, complete = school._fetch_reviews_for_professor(_prof())
    assert len(reviews) == 100 * fetch_lite.MAX_REVIEW_PAGES
    assert complete is True


def test_the_per_professor_review_cap_is_also_complete():
    """The other deliberate cap, exercised by setting it for one call."""
    import fetch_lite
    original = fetch_lite.MAX_REVIEWS_PER_PROFESSOR
    fetch_lite.MAX_REVIEWS_PER_PROFESSOR = 120
    try:
        school = _school([_page(100, True), _page(100, True)])
        reviews, complete = school._fetch_reviews_for_professor(_prof())
        assert len(reviews) == 120
        assert complete is True
    finally:
        fetch_lite.MAX_REVIEWS_PER_PROFESSOR = original


# ── a 200 that carries no ratings connection ────────────────────────────────
#
# _graphql_post returning None is the *transport* failure. RMP also soft-fails:
# HTTP 200 with an `errors` array and a null node, which is what throttling looks
# like once the session is warm. _parse_ratings reads both of those as "no
# ratings connection", and the loop's `if not new_reviews: break` then reported
# the partial list as complete — so the most likely truncation mode was the one
# the flag could not see.


def _soft_error_page(body=None):
    """HTTP 200 whose payload carries no ratings connection."""
    return body if body is not None else {"errors": [{"message": "rate limited"}]}


@pytest.mark.parametrize("body", [
    {"errors": [{"message": "rate limited"}]},   # no data key at all
    {"data": {"node": None}},                    # node explicitly null
    {"data": {"node": {}}},                      # node without ratings
    {"data": {"node": {"ratings": None}}},       # ratings explicitly null
])
def test_a_soft_error_mid_pagination_is_incomplete(body):
    school = _school([_page(100, True), _soft_error_page(body)])
    reviews, complete = school._fetch_reviews_for_professor(_prof())
    assert len(reviews) == 100
    assert complete is False, f"soft error {body} reported as a complete fetch"


def test_a_soft_error_on_the_first_page_is_incomplete():
    school = _school([_soft_error_page()])
    reviews, complete = school._fetch_reviews_for_professor(_prof())
    assert reviews == []
    assert complete is False


def test_a_soft_error_puts_the_professor_in_the_retry_pass():
    """The point of detecting it: it has to be re-fetched, not published."""
    school = _school([_page(100, True), _soft_error_page(),   # first pass
                      _page(100, True), _page(40, False)])    # retry: clean
    prof = _prof()
    _run_scrape(school, [prof])
    assert len(prof.reviews) == 140
    assert prof.reviews_complete is True


# ── what the retry pass selects on ──────────────────────────────────────────

def _run_scrape(school, profs):
    school.professors_list = profs
    school._scrape_all_reviews()


def test_retry_covers_truncated_professors_not_just_empty_ones(capsys):
    """A partial fetch is retried; the retry's full list replaces the partial one."""
    school = _school([_page(100, True), None,          # first pass: truncated
                      _page(100, True), _page(40, False)])  # retry: complete
    prof = _prof()
    _run_scrape(school, [prof])
    assert prof.reviews_complete is True
    assert len(prof.reviews) == 140


def test_a_complete_empty_professor_is_not_retried():
    """The old predicate (`not p.reviews`) re-fetched these hundreds of times."""
    school = _school([_page(0, has_next=False)])
    prof = _prof()
    _run_scrape(school, [prof])
    assert prof.reviews == []
    assert prof.reviews_complete is True
    assert school.calls["n"] == 1, "a complete fetch was retried"


def test_a_worse_retry_does_not_overwrite_a_longer_partial():
    """Keep whichever attempt saw more, so a retry cannot lose rows."""
    school = _school([_page(100, True), None,   # first pass: 100, truncated
                      _page(10, True), None])   # retry: 10, still truncated
    prof = _prof()
    _run_scrape(school, [prof])
    assert len(prof.reviews) == 100
    assert prof.reviews_complete is False


def test_a_shorter_complete_retry_does_not_overwrite_a_longer_partial():
    """The gap the `complete or ...` short-circuit left open.

    The condition above this reads "keep whichever attempt saw more", but
    `complete` was checked first, so a retry that came back complete replaced the
    first pass however little it saw. Worst case the retry's page 1 is empty:
    ([], True) overwrites 100 held reviews with none and marks the professor
    done, so nothing retries them and precompute publishes num_ratings 0.
    """
    school = _school([_page(100, True), None,     # first pass: 100, truncated
                      _page(10, False)])          # retry: 10, but complete
    prof = _prof()
    _run_scrape(school, [prof])
    assert len(prof.reviews) == 100, "a shorter complete retry overwrote good rows"
    assert prof.reviews_complete is False


def test_an_empty_complete_retry_never_zeroes_a_professor():
    """The worst case of the above, called out on its own: 100 rows -> 0."""
    school = _school([_page(100, True), None,     # first pass: 100, truncated
                      _page(0, has_next=False)])  # retry: complete, empty
    prof = _prof()
    _run_scrape(school, [prof])
    assert len(prof.reviews) == 100, "an empty retry zeroed a professor"
    assert prof.reviews_complete is False


def test_an_equal_length_complete_retry_upgrades_the_flag():
    """Same rows, now known complete — take it, so the retry pass can converge.

    Without the `>=` the professor stays incomplete forever and is re-fetched
    every run, which is the cost the old `not p.reviews` predicate already paid.
    """
    school = _school([_page(100, True), None,     # first pass: 100, truncated
                      _page(100, False)])         # retry: same 100, complete
    prof = _prof()
    _run_scrape(school, [prof])
    assert len(prof.reviews) == 100
    assert prof.reviews_complete is True


def test_still_incomplete_professors_are_named_in_the_output(capsys):
    """Counting them is not enough — the run has to say who.

    No corpus-wide row-count floor can see a single professor losing most of
    their ratings, so this print is the only signal before the load.
    """
    school = _school([_page(100, True), None, _page(100, True), None])
    prof = _prof(name="Ada Lovelace")
    _run_scrape(school, [prof])
    out = capsys.readouterr().out
    assert prof.reviews_complete is False
    assert "1 still incomplete" in out
    assert "Ada Lovelace" in out


def test_total_reviews_is_not_double_counted_across_passes(capsys):
    """The running total added the retry's reviews on top of the first attempt's."""
    school = _school([_page(100, True), None,
                      _page(100, True), _page(40, False)])
    _run_scrape(school, [_prof()])
    out = capsys.readouterr().out
    assert "Fetched 140 reviews" in out, out
