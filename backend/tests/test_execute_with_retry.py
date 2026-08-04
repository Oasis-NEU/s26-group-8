"""Tests for execute_with_retry.

CockroachDB runs serializable, so precompute's widest UPDATE (course_catalog
avg_rating, joining trace_courses x trace_scores over ~1.1M rows) can be aborted
with 40001 when it races the live site's reads. That actually happened on a test
rebuild: precompute died on that statement having already committed
professors_catalog, so the catalog was rebuilt while every course rating stayed
NULL. These tests pin the retry behaviour that prevents it.
"""

import psycopg2
import pytest

import precompute


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.rowcount = 4097

    def execute(self, sql, params=None):
        self._conn.executed.append(sql)
        err = self._conn.errors.pop(0) if self._conn.errors else None
        if err:
            raise err


class FakeConn:
    """Raises the queued errors on execute, then succeeds."""

    def __init__(self, errors, log):
        self.errors = errors
        self.log = log
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True
        self.log.append("commit")

    def rollback(self):
        self.rolled_back = True
        self.log.append("rollback")

    def close(self):
        self.closed = True
        self.log.append("close")


def _err(kind):
    """pgcode is read-only and only set by the server, so match on class."""
    return {
        "40001": psycopg2.errors.SerializationFailure,
        "40P01": psycopg2.errors.DeadlockDetected,
        "42601": psycopg2.errors.SyntaxError,
    }[kind]()


@pytest.fixture
def patched(monkeypatch):
    """Hands out a fresh FakeConn per _connect(), and makes sleep instant."""
    state = {"errors": [], "conns": [], "log": [], "sleeps": []}

    def fake_connect(*a, **kw):
        errs = [state["errors"].pop(0)] if state["errors"] else []
        c = FakeConn(errs, state["log"])
        state["conns"].append(c)
        state["log"].append("connect")
        return c

    monkeypatch.setattr(precompute, "_connect", fake_connect)
    monkeypatch.setattr(precompute.time, "sleep", lambda s: state["sleeps"].append(s))
    return state


def test_returns_rowcount_on_first_success(patched):
    assert precompute.execute_with_retry("UPDATE x") == 4097
    assert len(patched["conns"]) == 1
    assert patched["conns"][0].committed


def test_retries_serialization_failure_then_succeeds(patched):
    patched["errors"] = [_err("40001")]
    assert precompute.execute_with_retry("UPDATE x") == 4097
    assert len(patched["conns"]) == 2, "should have reconnected for the retry"
    assert patched["conns"][0].rolled_back
    assert patched["conns"][1].committed


def test_retries_deadlock(patched):
    patched["errors"] = [_err("40P01")]
    assert precompute.execute_with_retry("UPDATE x") == 4097
    assert len(patched["conns"]) == 2


def test_uses_a_fresh_connection_per_attempt(patched):
    # An aborted CRDB transaction poisons the session; reusing it fails again.
    patched["errors"] = [_err("40001"), _err("40001")]
    precompute.execute_with_retry("UPDATE x")
    assert len(patched["conns"]) == 3
    assert all(c.closed for c in patched["conns"]), "every attempt must close its conn"


def test_backoff_is_exponential(patched):
    patched["errors"] = [_err("40001"), _err("40001"), _err("40001")]
    precompute.execute_with_retry("UPDATE x")
    assert patched["sleeps"] == [2, 4, 8]


def test_non_retryable_error_raises_immediately(patched):
    # A syntax error must not be retried five times — fail fast.
    patched["errors"] = [_err("42601")]
    with pytest.raises(psycopg2.Error):
        precompute.execute_with_retry("UPDATE x")
    assert len(patched["conns"]) == 1
    assert patched["conns"][0].closed


def test_gives_up_after_the_attempt_limit(patched):
    patched["errors"] = [_err("40001")] * 5
    with pytest.raises(psycopg2.Error):
        precompute.execute_with_retry("UPDATE x", attempts=5)
    assert len(patched["conns"]) == 5
    assert patched["sleeps"] == [2, 4, 8, 16], "no sleep after the final attempt"


def test_connection_closed_even_on_success(patched):
    precompute.execute_with_retry("UPDATE x")
    assert patched["conns"][0].closed
