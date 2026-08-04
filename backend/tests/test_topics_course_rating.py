"""A topics code must not serve a single course-level rating.

49 course codes run under more than one title inside a single term — HONR3310 as
"Election 2024", "Honors Seminar" and "Language and Power" at once; ARTE3901 as
"ST: Assistive Design" and "ST: History of Comics". The code is a container for
unrelated classes, so averaging their TRACE scores together produces a number
that describes nothing. The page showed it anyway, and it also fed the
AggregateRating JSON-LD and the catalog sort.

precompute flags these (course_catalog.is_topics) and leaves avg_rating NULL for
them; the API suppresses avgRating/ratingCount and says so with isTopics. The
per-section and per-instructor ratings are untouched — those are real.

SELECT * plus .get("is_topics") means a catalog built before the column existed
still serves: absent reads as "not a topics code".
"""

import os

import pytest


def make_client(monkeypatch, catalog_row):
    os.environ.setdefault("CRDB_DATABASE_URL", "postgresql://stub")
    os.environ.setdefault("JWT_SECRET", "test-secret")
    import server

    monkeypatch.setattr(server, "_get_pool", lambda: (_ for _ in ()).throw(AssertionError("no DB in test")), raising=False)
    monkeypatch.setattr(server, "cache_get", lambda key: None, raising=False)
    monkeypatch.setattr(server, "cache_set", lambda key, data: None, raising=False)

    section = {
        "course_id": 1, "instructor_id": 10, "term_id": 200,
        "term_title": "Fall 2024", "department_name": "Honors Program",
        "display_name": "HONR3310:01 (Election 2024) - Ada Byron",
        "section": "01", "enrollment": 20,
        "instructor_first_name": "Ada", "instructor_last_name": "Byron",
    }

    def fake_query_one(sql, params=()):
        if "FROM course_catalog" in sql:
            return catalog_row
        raise AssertionError(f"unexpected query_one: {sql}")

    def fake_query(sql, params=()):
        if "FROM trace_courses" in sql:
            return [section]
        if "GROUP BY course_id, instructor_id, term_id" in sql:
            # One overall-question row: mean 4.0 across 10 responses.
            return [{
                "course_id": 1, "instructor_id": 10, "term_id": 200,
                "overall_weighted": 40.0, "overall_responses": 10, "overall_completed": 10,
                "challeng_weighted": 30.0, "challeng_responses": 10,
                "hours_weighted": 80.0, "hours_responses": 10,
            }]
        if "FROM professors_catalog" in sql:
            return [{"name_key": "ada byron", "slug": "ada-byron", "image_url": None,
                     "total_reviews": 5, "would_take_again_pct": 80.0,
                     "difficulty": 2.5, "rmp_rating": 4.1}]
        if "FROM rmp_reviews" in sql:
            return []
        if "GROUP BY question" in sql:
            return [{"question": "Overall Rating", "weighted_sum": 40.0, "total_responses": 10}]
        if "UNION ALL" in sql:
            return []
        raise AssertionError(f"unexpected query: {sql}")

    monkeypatch.setattr(server, "query_one", fake_query_one, raising=False)
    monkeypatch.setattr(server, "query", fake_query, raising=False)
    return server.app.test_client()


BASE = {"code": "HONR3310", "name": "Honors Seminar", "department": "Honors Program",
        "avg_rating": None, "num_responses": None}


def summary_for(monkeypatch, catalog_row):
    client = make_client(monkeypatch, catalog_row)
    resp = client.get("/api/courses/HONR3310")
    assert resp.status_code == 200
    return resp.get_json()


def test_topics_course_serves_no_course_level_rating(monkeypatch):
    data = summary_for(monkeypatch, {**BASE, "is_topics": True})
    assert data["summary"]["isTopics"] is True
    assert data["summary"]["avgRating"] is None
    # ratingCount is what AggregateRating JSON-LD keys off, so it has to go too.
    assert data["summary"]["ratingCount"] is None


def test_topics_course_keeps_its_per_instructor_ratings(monkeypatch):
    """Suppression is scoped to the blended course average. 'Ada Byron scored
    4.0 teaching this' is still a real measurement."""
    data = summary_for(monkeypatch, {**BASE, "is_topics": True})
    assert [i["avgRating"] for i in data["instructors"]] == [4.0]


def test_normal_course_still_serves_its_rating(monkeypatch):
    data = summary_for(monkeypatch, {**BASE, "is_topics": False})
    assert data["summary"]["isTopics"] is False
    assert data["summary"]["avgRating"] == 4.0
    assert data["summary"]["ratingCount"] == 10


def test_catalog_without_the_column_behaves_as_not_topics(monkeypatch):
    """Deploy ordering: the server can be live against a catalog built before
    is_topics existed. Absent must not read as True and must not 500."""
    data = summary_for(monkeypatch, dict(BASE))  # no is_topics key at all
    assert data["summary"]["isTopics"] is False
    assert data["summary"]["avgRating"] == 4.0
