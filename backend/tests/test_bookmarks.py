"""Unit tests for the injectable bookmarks SQL orchestration in bookmarks.py.

Uses fake query/query_one/write callables so nothing here touches a live DB —
these can run even while the shared CockroachDB cluster is unavailable.
"""

import datetime

from bookmarks import add_bookmark, remove_bookmark, list_bookmarks, item_exists


class FakeDb:
    """Fake query()/query_one()/write() with an in-memory bookmarks table and
    canned professors_catalog/course_catalog rows."""

    def __init__(self):
        self.bookmark_rows = []  # list of dicts: user_sub, item_type, item_key, created_at
        self.write_calls = []

        self.professors = {
            "olin-guha": {"slug": "olin-guha", "name": "Olin Guha", "department": "Khoury",
                          "college": "CS", "avg_rating": 4.2, "rmp_rating": 4.1,
                          "trace_rating": 4.3, "total_reviews": 31, "total_comments": 5,
                          "would_take_again_pct": 88.0, "image_url": None,
                          "focus_x": None, "focus_y": None},
        }
        self.courses = {
            "CS3500": {"code": "CS3500", "name": "Object-Oriented Design", "department": "Khoury",
                       "avg_rating": 4.0},
        }

    # ── query/query_one ──
    def query(self, sql, params=None):
        s = sql.lower()
        if "from bookmarks" in s:
            (user_sub,) = params
            rows = [r for r in self.bookmark_rows if r["user_sub"] == user_sub]
            return sorted(rows, key=lambda r: r["created_at"], reverse=True)
        if "from professors_catalog" in s and "= any" in s:
            (keys,) = params
            return [self.professors[k] for k in keys if k in self.professors]
        if "from course_catalog" in s and "= any" in s:
            (keys,) = params
            return [self.courses[k] for k in keys if k in self.courses]
        return []

    def query_one(self, sql, params=None):
        s = sql.lower()
        if "count(*)" in s and "from bookmarks" in s:
            (user_sub,) = params
            return {"cnt": sum(1 for r in self.bookmark_rows if r["user_sub"] == user_sub)}
        if "from professors_catalog" in s:
            (slug,) = params
            row = self.professors.get(slug)
            return {"exists": 1} if row else None
        if "from course_catalog" in s:
            (code,) = params
            row = self.courses.get(code)
            return {"exists": 1} if row else None
        return None

    # ── write ──
    def write(self, sql, params=None):
        self.write_calls.append((sql, params))
        s = sql.lower()
        if s.startswith("insert into bookmarks"):
            user_sub, item_type, item_key = params
            if any(r["user_sub"] == user_sub and r["item_type"] == item_type
                   and r["item_key"] == item_key for r in self.bookmark_rows):
                return  # ON CONFLICT DO NOTHING
            self.bookmark_rows.append({
                "user_sub": user_sub, "item_type": item_type, "item_key": item_key,
                "created_at": datetime.datetime.now(datetime.timezone.utc),
            })
        elif s.startswith("delete from bookmarks"):
            user_sub, item_type, item_key = params
            self.bookmark_rows = [
                r for r in self.bookmark_rows
                if not (r["user_sub"] == user_sub and r["item_type"] == item_type
                        and r["item_key"] == item_key)
            ]


def test_item_exists_true_for_known_professor_and_course():
    db = FakeDb()
    assert item_exists("professor", "olin-guha", db.query_one) is True
    assert item_exists("course", "CS3500", db.query_one) is True


def test_item_exists_false_for_unknown_key_or_type():
    db = FakeDb()
    assert item_exists("professor", "does-not-exist", db.query_one) is False
    assert item_exists("not-a-real-type", "olin-guha", db.query_one) is False


def test_add_bookmark_returns_not_found_for_nonexistent_item():
    db = FakeDb()
    status = add_bookmark("user-1", "professor", "does-not-exist", db.query_one, db.write)
    assert status == "not_found"
    assert db.bookmark_rows == []


def test_add_bookmark_creates_row_for_real_item():
    db = FakeDb()
    status = add_bookmark("user-1", "professor", "olin-guha", db.query_one, db.write)
    assert status == "ok"
    assert len(db.bookmark_rows) == 1
    assert db.bookmark_rows[0]["item_key"] == "olin-guha"


def _fill_bookmarks(db, user_sub, n):
    for i in range(n):
        db.bookmark_rows.append({
            "user_sub": user_sub, "item_type": "course", "item_key": f"FAKE{i}",
            "created_at": datetime.datetime.now(datetime.timezone.utc),
        })


def test_add_bookmark_rejected_at_cap():
    db = FakeDb()
    _fill_bookmarks(db, "user-1", 200)
    status = add_bookmark("user-1", "professor", "olin-guha", db.query_one, db.write)
    assert status == "limit_reached"
    assert len(db.bookmark_rows) == 200  # nothing inserted


def test_add_bookmark_allowed_just_below_cap():
    db = FakeDb()
    _fill_bookmarks(db, "user-1", 199)
    status = add_bookmark("user-1", "professor", "olin-guha", db.query_one, db.write)
    assert status == "ok"
    assert len(db.bookmark_rows) == 200


def test_bookmark_cap_is_per_user():
    db = FakeDb()
    _fill_bookmarks(db, "someone-else", 200)
    status = add_bookmark("user-1", "professor", "olin-guha", db.query_one, db.write)
    assert status == "ok"


def test_add_bookmark_is_idempotent():
    db = FakeDb()
    add_bookmark("user-1", "professor", "olin-guha", db.query_one, db.write)
    add_bookmark("user-1", "professor", "olin-guha", db.query_one, db.write)
    assert len(db.bookmark_rows) == 1


def test_remove_bookmark_deletes_row():
    db = FakeDb()
    add_bookmark("user-1", "course", "CS3500", db.query_one, db.write)
    assert len(db.bookmark_rows) == 1
    remove_bookmark("user-1", "course", "CS3500", db.write)
    assert db.bookmark_rows == []


def test_remove_bookmark_is_idempotent_when_missing():
    db = FakeDb()
    remove_bookmark("user-1", "course", "CS3500", db.write)  # no-op, should not raise
    assert db.bookmark_rows == []


def test_list_bookmarks_denormalizes_and_includes_bookmarked_at():
    db = FakeDb()
    add_bookmark("user-1", "professor", "olin-guha", db.query_one, db.write)
    add_bookmark("user-1", "course", "CS3500", db.query_one, db.write)

    result = list_bookmarks("user-1", db.query)

    assert len(result["professors"]) == 1
    assert result["professors"][0]["slug"] == "olin-guha"
    assert result["professors"][0]["name"] == "Olin Guha"
    assert "bookmarkedAt" in result["professors"][0]

    assert len(result["courses"]) == 1
    assert result["courses"][0]["code"] == "CS3500"
    assert "bookmarkedAt" in result["courses"][0]


def test_list_bookmarks_empty_for_user_with_none():
    db = FakeDb()
    result = list_bookmarks("nobody", db.query)
    assert result == {"professors": [], "courses": []}


def test_list_bookmarks_skips_keys_that_no_longer_resolve():
    db = FakeDb()
    # Bookmark exists in the bookmarks table but the professor has since been
    # dropped from professors_catalog (e.g. a precompute.py rebuild) — should
    # be silently skipped, not raise.
    db.bookmark_rows.append({
        "user_sub": "user-1", "item_type": "professor", "item_key": "ghost-prof",
        "created_at": datetime.datetime.now(datetime.timezone.utc),
    })
    result = list_bookmarks("user-1", db.query)
    assert result["professors"] == []
