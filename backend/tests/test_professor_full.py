"""Unit tests for the extracted, injectable /full SQL orchestration.

These prove the round-trip reduction (a fake `query` records every call) and
guard the unauthenticated response shape. server.py can't be imported in tests
(it needs live env vars), so the logic lives in professor_full.py with the same
injectable-`query` pattern as chat_retrieve.py.
"""

from professor_full import build_full, _resolve_professor


class RecordingQuery:
    """Fake query()/query_one() that records each SQL it is asked to run and
    returns canned rows based on which table the SQL targets."""

    def __init__(self):
        self.calls = []  # list of SQL strings, in order

    def _rows_for(self, sql):
        s = sql.lower()
        # Order matters: trace_comments/reddit before the generic trace_courses.
        if "from professors_catalog" in s:
            return [{"name": "Olin Guha", "slug": "olin-guha", "name_key": "olin guha",
                     "department": "Khoury", "rmp_rating": 4.1, "trace_rating": 4.3,
                     "avg_rating": 4.2, "difficulty": 3.5, "would_take_again_pct": 88.0,
                     "total_reviews": 31, "professor_url": None, "image_url": None,
                     "avg_hours": 6.0}]
        if "from rmp_reviews" in s:
            return [{"course": "CS3500", "quality": 5, "difficulty": 3, "date": "2024",
                     "tags": "", "attendance": "", "grade": "A", "textbook": "",
                     "online_class": "", "comment": "Great teacher."}]
        if "from trace_comments" in s:
            return [{"tc_term_id": 901, "tc_course_id": 1, "question": "Comments",
                     "comment": "Tough but fair."}]
        if "from reddit_mentions" in s:
            return [{"body": "guha is hard", "subreddit": "NEU", "permalink": "/r/x",
                     "created_utc": None, "reddit_score": 12, "sentiment": "negative",
                     "sentiment_score": -0.4}]
        if "from trace_scores" in s:
            # One overall + one challenge + one hours row for the same course/term.
            base = {"course_id": 1, "term_id": 901, "display_name": "CS3500: OOD"}
            return [
                {**base, "question": "Overall rating", "mean": 4.5,
                 "count_1": 0, "count_2": 0, "count_3": 1, "count_4": 2,
                 "count_5": 7, "completed": 10},
                {**base, "question": "How challenging", "mean": 3.5,
                 "count_1": 0, "count_2": 1, "count_3": 4, "count_4": 3,
                 "count_5": 2, "completed": 10},
                {**base, "question": "Hours per week", "mean": 6.0,
                 "count_1": 1, "count_2": 2, "count_3": 4, "count_4": 2,
                 "count_5": 1, "completed": 10},
            ]
        if "from trace_courses" in s:
            return [{"course_id": 1, "term_id": 901, "term_title": "Fall 2023",
                     "department_name": "Khoury", "display_name": "CS3500: OOD",
                     "section": "1", "enrollment": 40, "instructor_id": 7}]
        return []

    def query(self, sql, params=None):
        self.calls.append(sql)
        return self._rows_for(sql)

    def query_one(self, sql, params=None):
        self.calls.append(sql)
        rows = self._rows_for(sql)
        return rows[0] if rows else None

    # ── assertion helpers ──
    def count_hitting(self, table):
        return sum(1 for c in self.calls if table.lower() in c.lower())


def _fake_fetch_reddit_mentions(slug, query_fn):
    # Mirror server's fetch_reddit_mentions: a real round-trip through query().
    rows = query_fn("SELECT t.body, t.subreddit FROM reddit_mentions m "
                    "JOIN reddit_text t ON t.source_id = m.source_id "
                    "WHERE m.professor_slug = %s", (slug,))
    return [{"body": r.get("body") or "", "sentiment": r.get("sentiment"),
             "sentiment_score": r.get("sentiment_score"), "score": r.get("reddit_score"),
             "subreddit": r.get("subreddit"), "permalink": r.get("permalink"),
             "created_utc": r.get("created_utc")} for r in rows]


def _build(slug="olin-guha"):
    rq = RecordingQuery()
    data = build_full(slug, rq.query, rq.query_one, sanitize=lambda t: t,
                      fetch_reddit_mentions=_fake_fetch_reddit_mentions,
                      is_authed=False)
    return data, rq


# ── Round-trip reduction (the whole point) ──

def test_full_unauthed_makes_at_most_six_round_trips():
    _, rq = _build()
    assert len(rq.calls) <= 6, f"expected <=6 round-trips, got {len(rq.calls)}: {rq.calls}"


def test_catalog_looked_up_only_once():
    # professor_profile + professor_reviews used to each fetch the catalog row.
    _, rq = _build()
    assert rq.count_hitting("from professors_catalog") == 1


def test_trace_courses_fetched_only_once():
    # Both old functions fetched trace_courses by name_key separately.
    _, rq = _build()
    assert rq.count_hitting("from trace_courses") == 1


def test_trace_scores_scanned_only_once():
    # Old unauthed path ran 3 separate scans (challenge / overall / hours).
    _, rq = _build()
    assert rq.count_hitting("from trace_scores") == 1


# ── Response shape is preserved ──

def test_full_returns_profile_fields():
    data, _ = _build()
    assert data["name"] == "Olin Guha"
    assert data["department"] == "Khoury"
    assert data["avgRating"] == 4.2
    assert data["totalRatings"] == 31
    assert data["wouldTakeAgainPct"] == 88.0


def test_full_includes_reviews_trace_comments_and_reddit():
    data, _ = _build()
    assert "reviews" in data and "traceComments" in data and "redditMentions" in data
    assert data["reviews"][0]["course"] == "CS3500"
    # Unauthed: TRACE comment text is gated to "".
    assert data["traceComments"][0]["comment"] == ""
    assert data["redditMentions"][0]["sentiment"] == "negative"


def test_full_builds_trace_courses_with_hours_and_overall():
    data, _ = _build()
    courses = data["traceCourses"]
    assert len(courses) == 1
    c = courses[0]
    assert c["displayName"] == "CS3500: OOD"
    # hours weighted mean: (1*1+3.5*2+6*4+9*2+12*1)/(1+2+4+2+1)=62/10=6.2
    assert c["hoursPerWeek"] == 6.2


def test_full_rating_distribution_bucketed_by_course_code():
    data, _ = _build()
    dist = data["traceRatingCounts"]
    assert "CS3500" in dist
    assert dist["CS3500"]["count5"] == 7
    assert dist["CS3500"]["completed"] == 10


def test_full_blends_difficulty_from_rmp_and_trace():
    # rmp difficulty 3.5; trace challenge weighted mean:
    # (1*0+2*1+3*4+4*3+5*2)/(0+1+4+3+2)=36/10=3.6 → blended (3.5+3.6)/2=3.55→3.55
    data, _ = _build()
    assert data["difficulty"] == 3.55


def test_full_ratings_use_overall_course_not_law_overall_effectiveness():
    # Law sections carry TWO overall questions: 'Overall Course' and 'Overall
    # Effectiveness'. Ratings must count only 'Overall Course' — but the exclusion
    # must be exact, because the Bluera-era label ("What is your overall rating of
    # this instructor teaching effectiveness?") also contains "effectiveness" and
    # must keep counting.
    class LawQuery(RecordingQuery):
        def _rows_for(self, sql):
            s = sql.lower()
            if "from trace_scores" in s:
                base = {"course_id": 2, "term_id": 159, "display_name": "LAW6101: Con Law"}
                return [
                    {**base, "question": "Overall Course", "mean": 2.5,
                     "count_1": 1, "count_2": 0, "count_3": 0, "count_4": 1,
                     "count_5": 0, "completed": 2},
                    {**base, "question": "Overall Effectiveness", "mean": 3.0,
                     "count_1": 0, "count_2": 1, "count_3": 0, "count_4": 1,
                     "count_5": 0, "completed": 2},
                    {**base, "question": "What is your overall rating of this "
                     "instructor teaching effectiveness?", "mean": 4.5,
                     "count_1": 0, "count_2": 0, "count_3": 0, "count_4": 1,
                     "count_5": 1, "completed": 2},
                ]
            if "from trace_courses" in s:
                return [{"course_id": 2, "term_id": 159, "term_title": "Fall 2022 Law",
                         "department_name": "Law", "display_name": "LAW6101: Con Law",
                         "section": "1", "enrollment": 20, "instructor_id": 8}]
            return super()._rows_for(sql)

    rq = LawQuery()
    data = build_full("olin-guha", rq.query, rq.query_one, sanitize=lambda t: t,
                      fetch_reddit_mentions=_fake_fetch_reddit_mentions, is_authed=False)
    dist = data["traceRatingCounts"]["LAW6101"]
    assert dist["count4"] == 2, "Overall Course + Bluera overall only"
    assert dist["count2"] == 0, "Overall Effectiveness counts must not leak into ratings"
    assert dist["completed"] == 4


def test_full_404_when_professor_missing():
    rq = RecordingQuery()
    rq._rows_for = lambda sql: []  # nothing found
    result = build_full("nobody", rq.query, rq.query_one, sanitize=lambda t: t,
                        is_authed=False)
    assert result is None  # caller turns None into a 404


def test_resolve_professor_applies_alias_map_to_slug_fallback():
    # /professor/chris-bosso was live before "Chris Bosso" -> "Christopher Bosso"
    # was added to ALIAS_MAP; the slug->name_key fallback must apply the alias so
    # the old link still resolves instead of 404ing on the stale "chris bosso" key.
    calls = []

    def fake_query_one(sql, params=None):
        calls.append(params)
        if "where slug" in sql.lower():
            return None  # force the name_key fallback
        return {"name_key": "christopher bosso"}

    _resolve_professor("chris-bosso", fake_query_one)
    assert calls[-1] == ("christopher bosso",), calls
