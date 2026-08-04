"""Route-level tests for the /api/bookmarks endpoints in server.py.

Follows the test_departments_api.py pattern: env vars before `import server`,
monkeypatch the pool and the query/_write helpers so no live DB is touched.
Auth is exercised for real — tokens are signed with the same secret the
routes verify against.
"""

import datetime
import os

import jwt
import pytest

TEST_SECRET = "test-secret-0123456789abcdefghijklmnop"  # >=32 bytes for HS256  # gitleaks:allow


def make_token(sub="user-123", expired=False):
    delta = datetime.timedelta(days=-1 if expired else 7)
    return jwt.encode(
        {"sub": sub, "email": "t@husky.neu.edu",
         "exp": datetime.datetime.now(datetime.timezone.utc) + delta},
        TEST_SECRET, algorithm="HS256",
    )


def auth_headers(**kw):
    return {"Authorization": f"Bearer {make_token(**kw)}"}


@pytest.fixture
def bm_client(monkeypatch):
    os.environ.setdefault("CRDB_DATABASE_URL", "postgresql://stub")
    os.environ.setdefault("JWT_SECRET", "test-secret")
    import server
    monkeypatch.setattr(server, "JWT_SECRET", TEST_SECRET, raising=False)
    # Stop the real pool from ever opening a connection during this test.
    monkeypatch.setattr(server, "_get_pool", lambda: (_ for _ in ()).throw(AssertionError("no DB in test")), raising=False)
    monkeypatch.setattr(server, "cache_get", lambda key: None, raising=False)
    monkeypatch.setattr(server, "cache_set", lambda key, data: None, raising=False)

    state = {"bookmark_count": 0, "writes": []}
    created = datetime.datetime(2026, 7, 28, 12, 0, 0, tzinfo=datetime.timezone.utc)

    def fake_query(sql, params=()):
        if "FROM bookmarks" in sql and "item_type" in sql:
            return [
                {"item_type": "professor", "item_key": "alice-smith", "created_at": created},
                {"item_type": "course", "item_key": "CS2500", "created_at": created},
            ]
        if "FROM professors_catalog" in sql and "ANY" in sql:
            return [{"slug": "alice-smith", "name": "Alice Smith",
                     "department": "Computer Science", "college": "Khoury",
                     "avg_rating": 4.5, "rmp_rating": 4.4, "trace_rating": 4.6,
                     "total_reviews": 120, "total_comments": 12,
                     "would_take_again_pct": 90.0, "image_url": None,
                     "focus_x": None, "focus_y": None}]
        if "FROM course_catalog" in sql and "ANY" in sql:
            return [{"code": "CS2500", "name": "Fundamentals of Computer Science 1",
                     "department": "Computer Science", "avg_rating": 4.1}]
        raise AssertionError(f"unexpected query: {sql}")

    def fake_query_one(sql, params=()):
        low = sql.lower()
        if "count(*)" in low and "from bookmarks" in low:
            return {"cnt": state["bookmark_count"]}
        if "FROM professors_catalog" in sql:
            return {"x": 1} if params[0] == "alice-smith" else None
        if "FROM course_catalog" in sql:
            return {"x": 1} if params[0] == "CS2500" else None
        raise AssertionError(f"unexpected query_one: {sql}")

    def fake_write(sql, params=None):
        state["writes"].append((sql, params))

    def fake_write_all(statements):
        """The transactional add path. Recorded into the same list, in order, so
        assertions on writes[-1] still describe the last statement executed."""
        state["write_all_calls"].append(list(statements))
        for sql, params in statements:
            fake_write(sql, params)

    state["write_all_calls"] = []
    monkeypatch.setattr(server, "query", fake_query, raising=False)
    monkeypatch.setattr(server, "query_one", fake_query_one, raising=False)
    monkeypatch.setattr(server, "_write", fake_write, raising=False)
    monkeypatch.setattr(server, "_write_all", fake_write_all, raising=False)
    return server.app.test_client(), state


def test_all_bookmark_routes_require_auth(bm_client):
    client, _ = bm_client
    body = {"itemType": "professor", "itemKey": "alice-smith"}
    assert client.get("/api/bookmarks").status_code == 401
    assert client.post("/api/bookmarks", json=body).status_code == 401
    assert client.delete("/api/bookmarks", json=body).status_code == 401


def test_expired_token_is_rejected(bm_client):
    client, _ = bm_client
    resp = client.get("/api/bookmarks", headers=auth_headers(expired=True))
    assert resp.status_code == 401


def test_garbage_token_is_rejected(bm_client):
    client, _ = bm_client
    resp = client.get("/api/bookmarks", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_list_bookmarks_returns_catalog_shaped_rows(bm_client):
    client, _ = bm_client
    resp = client.get("/api/bookmarks", headers=auth_headers())
    assert resp.status_code == 200
    data = resp.get_json()
    assert [p["slug"] for p in data["professors"]] == ["alice-smith"]
    assert data["professors"][0]["avgRating"] == 4.5
    assert data["professors"][0]["bookmarkedAt"] == created_iso()
    assert [c["code"] for c in data["courses"]] == ["CS2500"]
    assert data["courses"][0]["bookmarkedAt"] == created_iso()


def created_iso():
    return datetime.datetime(2026, 7, 28, 12, 0, 0, tzinfo=datetime.timezone.utc).isoformat()


def test_add_invalid_type_is_400(bm_client):
    client, state = bm_client
    resp = client.post("/api/bookmarks",
                       json={"itemType": "playlist", "itemKey": "x"},
                       headers=auth_headers())
    assert resp.status_code == 400
    assert state["writes"] == []


def test_add_missing_key_is_400(bm_client):
    client, state = bm_client
    resp = client.post("/api/bookmarks",
                       json={"itemType": "professor"},
                       headers=auth_headers())
    assert resp.status_code == 400
    assert state["writes"] == []


def test_add_unknown_item_is_404(bm_client):
    client, state = bm_client
    resp = client.post("/api/bookmarks",
                       json={"itemType": "professor", "itemKey": "nobody-here"},
                       headers=auth_headers())
    assert resp.status_code == 404
    assert state["writes"] == []


def test_add_writes_idempotent_insert(bm_client):
    client, state = bm_client
    resp = client.post("/api/bookmarks",
                       json={"itemType": "professor", "itemKey": "alice-smith"},
                       headers=auth_headers())
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    sql, params = state["writes"][-1]
    assert "INSERT INTO bookmarks" in sql
    assert "ON CONFLICT" in sql
    assert params == ("user-123", "professor", "alice-smith")


def test_add_rejected_at_cap_is_400(bm_client):
    client, state = bm_client
    state["bookmark_count"] = 200
    resp = client.post("/api/bookmarks",
                       json={"itemType": "professor", "itemKey": "alice-smith"},
                       headers=auth_headers())
    assert resp.status_code == 400
    assert "limit" in resp.get_json()["error"].lower()
    assert state["writes"] == []


def test_delete_writes_delete(bm_client):
    client, state = bm_client
    resp = client.delete("/api/bookmarks",
                         json={"itemType": "course", "itemKey": "CS2500"},
                         headers=auth_headers())
    assert resp.status_code == 200
    sql, params = state["writes"][-1]
    assert "DELETE FROM bookmarks" in sql
    assert params == ("user-123", "course", ["CS2500"])


def test_delete_invalid_type_is_400(bm_client):
    client, state = bm_client
    resp = client.delete("/api/bookmarks",
                         json={"itemType": "playlist", "itemKey": "x"},
                         headers=auth_headers())
    assert resp.status_code == 400
    assert state["writes"] == []


# ── server._write_all: the actual transaction ────────────────────────────────
# The fixture above stubs this out, so the real helper needs its own coverage:
# one commit for the whole list, and a rollback that re-raises rather than
# leaving a half-applied bookmark mutation committed.

os.environ.setdefault("CRDB_DATABASE_URL", "postgresql://stub")
os.environ.setdefault("JWT_SECRET", "test-secret")
import psycopg2  # noqa: E402
import server as server_mod  # noqa: E402


class FakeConn:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on      # substring of the statement that should raise
        self.executed, self.commits, self.rollbacks = [], 0, 0

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("statement failed")
        self.executed.append((sql, params))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


STATEMENTS = [("INSERT INTO bookmarks ...", ("u", "professor", "a")),
              ("DELETE FROM bookmarks ...", ("u", "professor", ["b"]))]


def test_write_all_commits_once_for_the_whole_batch(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(server_mod, "get_db", lambda: conn, raising=False)
    server_mod._write_all(STATEMENTS)
    assert [sql for sql, _ in conn.executed] == [sql for sql, _ in STATEMENTS]
    assert conn.commits == 1, "a commit per statement would not be one transaction"
    assert conn.rollbacks == 0


def test_write_all_rolls_back_and_reraises(monkeypatch):
    conn = FakeConn(fail_on="DELETE")
    monkeypatch.setattr(server_mod, "get_db", lambda: conn, raising=False)
    with pytest.raises(RuntimeError):
        server_mod._write_all(STATEMENTS)
    assert conn.rollbacks == 1
    assert conn.commits == 0, "the successful INSERT must not be committed alone"


def test_write_all_retries_once_on_a_stale_connection(monkeypatch):
    """Mirrors query()/_write: a recycled pool connection must not fail the request.

    Both statements are idempotent, so replaying the whole batch is safe.
    """
    conns = [FakeConn(fail_on="INSERT"), FakeConn()]
    conns[0].execute = lambda sql, params=None: (_ for _ in ()).throw(
        psycopg2.InterfaceError("connection already closed"))
    discarded = []
    monkeypatch.setattr(server_mod, "get_db", lambda: conns[len(discarded)], raising=False)
    monkeypatch.setattr(server_mod, "_discard_db_conn", lambda: discarded.append(1), raising=False)

    server_mod._write_all(STATEMENTS)
    assert discarded == [1], "the stale connection must be discarded"
    assert conns[1].commits == 1
