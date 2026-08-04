"""A failed ratings request must never look like "this professor has no reviews".

RMP serves zero rating nodes for some professors whose summary counters still
claim numRatings >= 1 — a rating was deleted and the aggregate was never
recalculated, so its own website prints "doesn't have any ratings yet" next to an
average. Five Northeastern professors are in that state.

That made an empty result ambiguous, and the scraper treated a rate-limited or
errored request the same way: `_graphql_post` returned None, the page loop broke,
and the professor came back with whatever had been collected so far. Reviews went
missing with no error, no log line, and a row count that still looked plausible.
Now a request failure raises and an empty-but-successful fetch does not.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
import fetch_lite as F  # noqa: E402
from models import Professor  # noqa: E402


def _prof(name="Test Prof", n="5"):
    return Professor(name=name, num_ratings=n, graphql_id="VGVhY2hlci0x")


def _page(count, has_next, cursor="c1"):
    """A ratings response carrying `count` rating nodes."""
    return {"data": {"node": {"ratings": {
        "edges": [{"cursor": cursor, "node": {
            "comment": f"review {i}", "class": "CS1000",
            "date": "2026-01-01 00:00:00 +0000 UTC", "qualityRating": 4,
            "difficultyRatingRounded": 3, "ratingTags": "", "grade": "A",
            "isForOnlineClass": False, "attendanceMandatory": "mandatory",
            "textbookIsUsed": False,
        }} for i in range(count)],
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
    }}}}


def _school(responses):
    """An RMPSchool whose _graphql_post replays `responses` in order."""
    school = F.RMPSchool.__new__(F.RMPSchool)          # skip __init__/network
    calls = []

    def fake_post(payload, retries=2):
        calls.append(payload)
        return responses[len(calls) - 1] if len(calls) <= len(responses) else None

    school._graphql_post = fake_post
    school.calls = calls
    return school


# ── the failure case that used to be silent ─────────────────────────────────

def test_request_failure_raises_instead_of_returning_partial_data():
    # Page 1 succeeds with more pages pending, page 2 fails (429 / network).
    school = _school([_page(2, True), None])
    with pytest.raises(F.ReviewFetchError) as e:
        school._fetch_reviews_for_professor(_prof())
    assert "page 2" in str(e.value)
    assert "2 reviews collected" in str(e.value), "must say how much was lost"


def test_first_page_failure_raises():
    school = _school([None])
    with pytest.raises(F.ReviewFetchError):
        school._fetch_reviews_for_professor(_prof())


def test_page_limit_with_more_pending_raises():
    # Silently truncating at the safety limit is the same class of data loss.
    school = _school([_page(1, True) for _ in range(F.MAX_REVIEW_PAGES + 2)])
    with pytest.raises(F.ReviewFetchError) as e:
        school._fetch_reviews_for_professor(_prof())
    assert "page limit" in str(e.value)


# ── the legitimate empty case must NOT raise ────────────────────────────────

def test_professor_with_no_ratings_returns_empty_without_raising():
    # The 5 real professors: RMP claims a rating, serves no rating nodes.
    school = _school([_page(0, False)])
    assert school._fetch_reviews_for_professor(_prof("Beth Cohen", "1")) == []


def test_last_page_ending_cleanly_does_not_raise():
    school = _school([_page(3, False)])
    assert len(school._fetch_reviews_for_professor(_prof())) == 3


def test_pagination_stops_without_an_extra_request_after_the_last_page():
    school = _school([_page(2, True), _page(2, False)])
    assert len(school._fetch_reviews_for_professor(_prof())) == 4
    assert len(school.calls) == 2, "must not request a page RMP said doesn't exist"


def test_all_pages_are_collected():
    school = _school([_page(100, True), _page(100, True), _page(37, False)])
    assert len(school._fetch_reviews_for_professor(_prof())) == 237


# ── same-named professors must not share bookkeeping ────────────────────────
#
# 49 of the 3,892 scraped professors share a name with a different RMP profile
# page (Rick Arrowood has three). Keying the pass/fail state by name let one
# namesake's result overwrite the other's, which is how a real failure got
# relabelled as a phantom and how already-fetched reviews got wiped.

def _scrape(profs, fetch):
    """Run _scrape_all_reviews with the per-professor fetch stubbed out."""
    school = F.RMPSchool.__new__(F.RMPSchool)          # skip __init__/network
    school.professors_list = profs
    school.failed_review_fetches = {}
    school._fetch_reviews_for_professor = fetch
    school._scrape_all_reviews()
    return school


def _twins():
    """Two distinct RMP profile pages that happen to carry the same name."""
    return (Professor(name="Rick Arrowood", num_ratings="19", graphql_id="VGVhY2hlci0x"),
            Professor(name="Rick Arrowood", num_ratings="1", graphql_id="VGVhY2hlci0y"))


def test_namesakes_failure_is_not_erased_by_the_others_success():
    good, bad = _twins()

    def fetch(prof):
        if prof.graphql_id == bad.graphql_id:
            raise F.ReviewFetchError(f"{prof.name}: ratings request failed on page 1")
        return ["r1", "r2", "r3"]

    school = _scrape([good, bad], fetch)

    assert list(school.failed_review_fetches) == [bad.graphql_id], \
        "the namesake that never succeeded must still be reported as missing data"
    assert good.reviews == ["r1", "r2", "r3"], "the one that worked keeps its reviews"
    assert bad.reviews == []


def test_namesakes_success_is_not_wiped_by_the_others_retry():
    good, flaky = _twins()
    attempts = {}

    def fetch(prof):
        attempts[prof.graphql_id] = attempts.get(prof.graphql_id, 0) + 1
        if prof.graphql_id == flaky.graphql_id and attempts[prof.graphql_id] == 1:
            raise F.ReviewFetchError(f"{prof.name}: ratings request failed on page 1")
        return ["r1", "r2"]

    school = _scrape([good, flaky], fetch)

    assert school.failed_review_fetches == {}, "the retry recovered the flaky profile"
    assert good.reviews == ["r1", "r2"]
    assert flaky.reviews == ["r1", "r2"]
    assert attempts[good.graphql_id] == 1, \
        "a namesake's retry must not drag in a professor that already succeeded"


def test_phantom_and_failure_are_told_apart_across_namesakes():
    # One profile legitimately serves no ratings, its namesake errors outright.
    phantom, broken = _twins()

    def fetch(prof):
        if prof.graphql_id == broken.graphql_id:
            raise F.ReviewFetchError(f"{prof.name}: ratings request failed on page 1")
        return []

    school = _scrape([phantom, broken], fetch)

    assert list(school.failed_review_fetches) == [broken.graphql_id], \
        "an empty-but-successful fetch must not absorb its namesake's failure"
