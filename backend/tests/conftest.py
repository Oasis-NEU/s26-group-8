import os

import pytest

# Test modules pin the JWT secret with os.environ.setdefault("JWT_SECRET",
# "test-secret"), which silently loses whenever a real value is already in the
# environment — and importing server or precompute calls load_dotenv(), which
# injects the developer's real .env. Whether the suite passed then came down to
# alphabetical collection order: if a module calling load_dotenv() sorted before
# the module doing the setdefault, every token signed with "test-secret" failed
# to validate and the auth-gated tests 401'd. conftest is imported before any
# test module, so pinning it here makes the suite order- and .env-independent.
# load_dotenv() does not override existing vars, so this value survives.
os.environ["JWT_SECRET"] = "test-secret"

# migrate_to_crdb reads the DB URL at import and sys.exits when it is missing,
# so importing it for a pure-logic test (REPLACE_ALLOWED, TABLES) takes the whole
# collection down wherever backend/.env is absent — which is every CI run, since
# ci.yml only checks the repo out. Set here rather than in the test modules for
# the same reason JWT_SECRET is: conftest is imported first, and load_dotenv()
# does not override an existing var. A deliberately unusable value — nothing in
# the suite connects, and a real URL here would let a stray test reach prod.
os.environ["CRDB_DATABASE_URL"] = "postgresql://test:test@localhost:26257/test"


@pytest.fixture
def render_client(monkeypatch):
    """A Flask test client for the render blueprint with the server's data
    view functions stubbed, so no DB is needed."""
    import render

    # Stubs returning Flask-like JSON via a tiny fake response object.
    class FakeResp:
        def __init__(self, data, status=200):
            self._data = data
            self.status_code = status
        def get_json(self):
            return self._data

    def fake_professor_profile(slug):
        if slug == "missing":
            return ({"error": "not found"}, 404)
        return FakeResp({
            "name": "Francis Georges", "department": "Economics",
            "avgRating": 4.25, "totalRatings": 2686, "wouldTakeAgainPct": 83,
            "difficulty": 2.9, "rmpRating": 4.3, "traceRating": 4.2,
            "imageUrl": None, "professorUrl": None, "traceCourses": [],
        })

    def fake_professor_reviews(slug):
        # Unauthenticated shape: RMP reviews carry text; TRACE comments are
        # present (so their count is known) but their text is gated to "".
        return FakeResp({"reviews": [
            {"course": "ECON1115", "quality": 5, "difficulty": 3,
             "date": "2024", "comment": "Excellent lecturer."}
        ], "traceComments": [
            {"question": "Comments", "comment": "", "termId": 901, "courseId": 1},
            {"question": "Comments", "comment": "", "termId": 902, "courseId": 1},
            {"question": "Comments", "comment": "", "termId": 903, "courseId": 1},
        ]})

    def fake_course_detail(code):
        if code == "missing":
            return ({"error": "not found"}, 404)
        return FakeResp({
            "summary": {"code": "ECON1115", "name": "Macroeconomics",
                        "department": "Economics", "avgRating": 4.1,
                        "avgEnrollment": 120, "latestTermTitle": "Fall 2025"},
            "instructors": [{"name": "Francis Georges", "slug": "francis-georges"}],
            "sections": [], "questionScores": [],
        })

    def fake_stats():
        return FakeResp([
            {"label": "Professors", "value": "9.3K"},
            {"label": "Courses", "value": "5K"},
            {"label": "Comments", "value": "120K"},
            {"label": "Departments", "value": "180"},
        ])

    def fake_professors_catalog():
        return FakeResp({"professors": [
            {"name": "Francis Georges", "slug": "francis-georges",
             "department": "Economics", "avgRating": 4.25},
        ], "total": 9329, "page": 1, "totalPages": 466})

    def fake_courses_catalog():
        return FakeResp({"courses": [
            {"code": "ECON1115", "name": "Macroeconomics",
             "department": "Economics", "avgRating": 4.1},
        ], "total": 5013, "page": 1, "totalPages": 251})

    def fake_departments_hub():
        return FakeResp({"departments": [
            {"slug": "computer-science", "name": "Computer Science",
             "professorCount": 214, "avgRating": 3.9},
        ], "total": 80})

    def fake_department_hub_detail(slug):
        if slug == "missing":
            return ({"error": "not found"}, 404)
        return FakeResp({
            "name": "Computer Science", "slug": "computer-science",
            "professorCount": 1, "avgRating": 4.25,
            "professors": [
                {"name": "Francis Georges", "slug": "francis-georges",
                 "avgRating": 4.25, "difficulty": 2.9,
                 "wouldTakeAgainPct": 83, "totalRatings": 2686},
            ],
        })

    monkeypatch.setattr(render, "_get_profile_view", lambda: fake_professor_profile, raising=False)
    monkeypatch.setattr(render, "_get_reviews_view", lambda: fake_professor_reviews, raising=False)
    monkeypatch.setattr(render, "_get_course_view", lambda: fake_course_detail, raising=False)
    monkeypatch.setattr(render, "_get_stats_view", lambda: fake_stats, raising=False)
    monkeypatch.setattr(render, "_get_professors_catalog_view", lambda: fake_professors_catalog, raising=False)
    monkeypatch.setattr(render, "_get_courses_catalog_view", lambda: fake_courses_catalog, raising=False)
    monkeypatch.setattr(render, "_get_departments_hub_view", lambda: fake_departments_hub, raising=False)
    monkeypatch.setattr(render, "_get_department_hub_detail_view", lambda: fake_department_hub_detail, raising=False)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(render.render_bp)
    return app.test_client()
