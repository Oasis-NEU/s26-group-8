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
