"""A truncated professor search must never look like a complete one.

Phase 2 already fails loud (see test_review_fetch_integrity.py). Phase 1 did
not: `_collect_professors` broke out of pagination on any failed request and
returned whatever it had, and `main()` only aborted at zero professors. That
mattered more than the review case, because precompute drops and rebuilds
professors_catalog from this CSV — a short page means professors disappear from
the live site — and the workflow then force-pushes the truncated CSV over the
data store as an orphan commit, so there is nothing to recover from.

The arithmetic at the time this was written: 3,892 professors is four pages of
1,000, the last holding 892. Losing that last page left exactly 3,000, and the
workflow floor was `-lt 3000`. It passed by one row.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))
import fetch_lite as F  # noqa: E402


def _node(i):
    return {
        "id": f"VGVhY2hlci0{i}", "legacyId": 1000 + i,
        "firstName": "Test", "lastName": f"Prof{i}",
        "department": "Computer Science",
        "school": {"id": "U2Nob29sLTY5Ng==", "name": "Northeastern University"},
        "avgRating": 4.2, "numRatings": 7,
        "avgDifficulty": 3.1, "wouldTakeAgainPercent": 88.0,
    }


def _page(count, has_next, cursor="c1"):
    """A teacher-search response carrying `count` professor nodes."""
    return {"data": {"search": {"teachers": {
        "didFallback": False,
        "edges": [{"cursor": cursor, "node": _node(i)} for i in range(count)],
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
    }}}}


def _school(responses):
    """An RMPSchool whose _graphql_post replays `responses` in order."""
    school = F.RMPSchool.__new__(F.RMPSchool)          # skip __init__/network
    school.school_id = 696
    school.school_name = "Unknown School"
    school.professors_list = []
    school.failed_review_fetches = {}
    school._graphql_school_id = "U2Nob29sLTY5Ng=="
    calls = []

    def fake_post(payload, retries=2):
        calls.append(payload)
        return responses[len(calls) - 1] if len(calls) <= len(responses) else None

    school._graphql_post = fake_post
    school.calls = calls
    return school


# ── the failure case that used to be silent ─────────────────────────────────

def test_request_failure_mid_pagination_raises():
    # Page 1 succeeds with more pages pending, page 2 fails (429 / network).
    school = _school([_page(3, True), None])
    with pytest.raises(F.ProfessorFetchError) as e:
        school._collect_professors()
    assert "page 2" in str(e.value)
    assert "3 professors" in str(e.value), "must say how much was lost"


def test_first_page_failure_raises():
    school = _school([None])
    with pytest.raises(F.ProfessorFetchError):
        school._collect_professors()


def test_unexpected_response_shape_raises():
    # An RMP schema change or an HTML error page parsed as JSON.
    school = _school([_page(3, True), {"data": {"search": {}}}])
    with pytest.raises(F.ProfessorFetchError) as e:
        school._collect_professors()
    assert "unexpected response" in str(e.value).lower()


def test_page_limit_with_more_pending_raises():
    school = _school([_page(1, True) for _ in range(F.MAX_SEARCH_PAGES + 2)])
    with pytest.raises(F.ProfessorFetchError) as e:
        school._collect_professors()
    assert "search limit" in str(e.value)
    assert "10 professors" in str(e.value)


def test_empty_page_while_rmp_says_more_pages_exist_raises():
    # Pagination is broken: no rows, yet a next page is promised.
    school = _school([_page(2, True), _page(0, True)])
    with pytest.raises(F.ProfessorFetchError) as e:
        school._collect_professors()
    assert "no professors" in str(e.value).lower()


# ── legitimate completions must NOT raise ───────────────────────────────────

def test_single_clean_page_does_not_raise():
    school = _school([_page(3, False)])
    school._collect_professors()
    assert len(school.professors_list) == 3


def test_all_pages_are_collected():
    school = _school([_page(4, True), _page(4, True), _page(2, False)])
    school._collect_professors()
    assert len(school.professors_list) == 10


def test_pagination_stops_without_an_extra_request_after_the_last_page():
    school = _school([_page(2, True), _page(2, False)])
    school._collect_professors()
    assert len(school.calls) == 2, "must not request a page RMP said doesn't exist"


def test_school_with_no_professors_at_all_does_not_raise():
    # Not a failure mode for school 696, but an empty first page that RMP says
    # is final is a complete answer. main() rejects the empty result separately.
    school = _school([_page(0, False)])
    school._collect_professors()
    assert school.professors_list == []


def test_school_name_is_picked_up_from_the_first_node():
    school = _school([_page(1, False)])
    school._collect_professors()
    assert school.school_name == "Northeastern University"
