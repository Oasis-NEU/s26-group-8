"""Unit tests for the injectable bookmarks SQL orchestration in bookmarks.py.

Uses fake query/query_one/write callables so nothing here touches a live DB —
these can run even while the shared CockroachDB cluster is unavailable.
"""

import datetime

import pytest

from bookmarks import (
    BOOKMARK_CAP, add_bookmark, remove_bookmark, list_bookmarks, item_exists)


class FakeDb:
    """Fake query()/query_one()/write() with an in-memory bookmarks table and
    canned professors_catalog/course_catalog rows."""

    def __init__(self):
        self.bookmark_rows = []  # list of dicts: user_sub, item_type, item_key, created_at
        self.write_calls = []
        self.write_all_calls = []

        self.professors = {
            "olin-guha": {"slug": "olin-guha", "name": "Olin Guha", "department": "Khoury",
                          "college": "CS", "avg_rating": 4.2, "rmp_rating": 4.1,
                          "trace_rating": 4.3, "total_reviews": 31, "total_comments": 5,
                          "would_take_again_pct": 88.0, "image_url": None,
                          "focus_x": None, "focus_y": None},
            # Renamed by an ALIAS_MAP entry, so "dan-koloski" is a retired slug.
            "daniel-koloski": {"slug": "daniel-koloski", "name": "Daniel Koloski",
                               "department": "Analytics", "college": "Professional Studies",
                               "avg_rating": 4.5, "rmp_rating": 4.4, "trace_rating": 4.6,
                               "total_reviews": 40, "total_comments": 9,
                               "would_take_again_pct": 90.0, "image_url": None,
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
            # The cap counts what will exist after the add, so the query excludes
            # item_key and any slug retired onto it. Modelled here rather than
            # ignored, or the cap tests would not exercise the real predicate.
            user_sub, item_type, superseded = params
            return {"cnt": sum(1 for r in self.bookmark_rows
                               if r["user_sub"] == user_sub
                               and not (r["item_type"] == item_type
                                        and r["item_key"] in superseded))}
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
    def write_all(self, statements):
        """The transactional path server.py uses: all-or-nothing, one commit.

        Applied to a copy so a mid-list failure leaves no partial state, which is
        the property _write_all's rollback provides against the real DB.
        """
        self.write_all_calls.append(list(statements))
        snapshot = list(self.bookmark_rows)
        try:
            for sql, params in statements:
                self.write(sql, params)
        except Exception:
            self.bookmark_rows = snapshot
            raise

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
            # item_key = ANY(list): a professor delete also matches slugs retired
            # by an ALIAS_MAP rename, since the client sends the current slug.
            user_sub, item_type, item_keys = params
            self.bookmark_rows = [
                r for r in self.bookmark_rows
                if not (r["user_sub"] == user_sub and r["item_type"] == item_type
                        and r["item_key"] in item_keys)
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


def _retired_row(db, user_sub, retired_slug):
    """A bookmark stored before an ALIAS_MAP entry renamed that professor."""
    db.bookmark_rows.append({
        "user_sub": user_sub, "item_type": "professor", "item_key": retired_slug,
        "created_at": datetime.datetime.now(datetime.timezone.utc),
    })


def test_add_bookmark_collapses_a_row_left_under_a_retired_slug():
    """Bookmarking a renamed professor you already hold must leave one row.

    list_bookmarks follows a retired slug to the current one, so both rows render
    as the same professor and the stale one is invisible — but it still occupies
    one of the user's BOOKMARK_CAP slots, and nothing but an unbookmark ever
    cleared it.
    """
    db = FakeDb()
    _retired_row(db, "user-1", "dan-koloski")
    status = add_bookmark("user-1", "professor", "daniel-koloski", db.query_one, db.write)
    assert status == "ok"
    assert [r["item_key"] for r in db.bookmark_rows] == ["daniel-koloski"]


def test_collapsing_leaves_other_users_rows_alone():
    db = FakeDb()
    _retired_row(db, "someone-else", "dan-koloski")
    add_bookmark("user-1", "professor", "daniel-koloski", db.query_one, db.write)
    assert {r["user_sub"] for r in db.bookmark_rows} == {"someone-else", "user-1"}


def test_a_professor_with_no_retired_slug_needs_no_delete():
    db = FakeDb()
    add_bookmark("user-1", "professor", "olin-guha", db.query_one, db.write)
    assert not any("DELETE" in sql for sql, _ in db.write_calls)


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


# ── atomicity of the collapse-plus-insert ───────────────────────────────────
# add_bookmark mutates two rows: it inserts the new bookmark and drops any row the
# user still holds under a slug retired onto it. Those used to be two independent
# commits with the DELETE first, so an insert that failed left the user with
# neither — their existing bookmark deleted and nothing put in its place.

def _statements(db):
    return [sql.split()[0].upper() for sql, _ in db.write_calls]


def test_add_sends_both_mutations_as_one_transaction():
    db = FakeDb()
    _retired_row(db, "user-1", "dan-koloski")
    add_bookmark("user-1", "professor", "daniel-koloski", db.query_one, db.write,
                 write_all=db.write_all)
    assert len(db.write_all_calls) == 1, "both mutations must go in one transaction"
    assert _statements(db) == ["INSERT", "DELETE"]
    assert [r["item_key"] for r in db.bookmark_rows] == ["daniel-koloski"]


def _write_all_failing_on(db, doomed):
    """A write_all whose `doomed` statement raises, rolling the batch back.

    Mirrors server._write_all: execute in order, commit once, roll back and
    re-raise on any failure.
    """
    def write_all(statements):
        db.write_all_calls.append(list(statements))
        snapshot = list(db.bookmark_rows)
        try:
            for sql, params in statements:
                if sql.lower().startswith(doomed):
                    raise RuntimeError(f"connection died before {doomed}")
                db.write(sql, params)
        except Exception:
            db.bookmark_rows = snapshot
            raise
    return write_all


def test_a_failed_insert_does_not_cost_the_user_their_bookmark():
    """The regression, in its original form: the insert never lands.

    Ordering alone is enough here — the DELETE is queued behind the INSERT, so it
    is never reached.
    """
    db = FakeDb()
    _retired_row(db, "user-1", "dan-koloski")
    with pytest.raises(RuntimeError):
        add_bookmark("user-1", "professor", "daniel-koloski", db.query_one, db.write,
                     write_all=_write_all_failing_on(db, "insert"))
    assert [r["item_key"] for r in db.bookmark_rows] == ["dan-koloski"], \
        "the bookmark the insert was meant to replace must survive"


def test_a_failure_after_the_insert_rolls_the_whole_batch_back():
    """What the transaction buys over ordering alone.

    The INSERT has already succeeded when the DELETE fails, so without a rollback
    the user would be left holding both rows.
    """
    db = FakeDb()
    _retired_row(db, "user-1", "dan-koloski")
    with pytest.raises(RuntimeError):
        add_bookmark("user-1", "professor", "daniel-koloski", db.query_one, db.write,
                     write_all=_write_all_failing_on(db, "delete"))
    assert [r["item_key"] for r in db.bookmark_rows] == ["dan-koloski"], \
        "a partly-applied add must leave the collection exactly as it was"


def test_without_a_transaction_the_insert_still_goes_first():
    """The sequential fallback cannot lose a bookmark either.

    Ordering, not just the transaction, is what rules out the unrecoverable case:
    a partial apply may leave a duplicate stale row (invisible, collapsed by the
    next add) but never a deleted bookmark with no replacement.
    """
    db = FakeDb()
    _retired_row(db, "user-1", "dan-koloski")
    add_bookmark("user-1", "professor", "daniel-koloski", db.query_one, db.write)
    assert _statements(db)[0] == "INSERT"
    assert db.write_all_calls == []
    assert [r["item_key"] for r in db.bookmark_rows] == ["daniel-koloski"]


# ── the cap now counts what will exist, not what does ───────────────────────

def test_cap_ignores_the_rows_this_add_will_collapse():
    """A user at the cap whose slot is held by a retired duplicate can still add.

    The collapse frees the slot, so counting it against them would reject an add
    that does not actually grow their collection.
    """
    db = FakeDb()
    _fill_bookmarks(db, "user-1", BOOKMARK_CAP - 1)
    _retired_row(db, "user-1", "dan-koloski")   # now exactly at the cap
    assert add_bookmark("user-1", "professor", "daniel-koloski",
                        db.query_one, db.write, write_all=db.write_all) == "ok"
    assert sum(1 for r in db.bookmark_rows if r["user_sub"] == "user-1") == BOOKMARK_CAP


def test_re_adding_an_existing_bookmark_at_the_cap_is_still_ok():
    # It cannot grow the collection (ON CONFLICT DO NOTHING), so refusing it would
    # report "limit_reached" for a bookmark the user already holds.
    db = FakeDb()
    _fill_bookmarks(db, "user-1", BOOKMARK_CAP - 1)
    add_bookmark("user-1", "professor", "olin-guha", db.query_one, db.write,
                 write_all=db.write_all)
    assert sum(1 for r in db.bookmark_rows if r["user_sub"] == "user-1") == BOOKMARK_CAP
    assert add_bookmark("user-1", "professor", "olin-guha", db.query_one, db.write,
                        write_all=db.write_all) == "ok"


def test_a_genuinely_full_collection_is_still_refused():
    db = FakeDb()
    _fill_bookmarks(db, "user-1", BOOKMARK_CAP)
    assert add_bookmark("user-1", "professor", "olin-guha", db.query_one, db.write,
                        write_all=db.write_all) == "limit_reached"
    assert db.write_all_calls == [], "nothing may be written once the cap is hit"
