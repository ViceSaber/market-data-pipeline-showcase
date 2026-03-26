"""Tests for SQLite retry/backoff helpers."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import is_lock_error, run_with_retry


def test_is_lock_error_matches_sqlite_lock_messages():
    assert is_lock_error(sqlite3.OperationalError("database is locked")) is True
    assert is_lock_error(sqlite3.OperationalError("database is busy")) is True
    assert is_lock_error(sqlite3.OperationalError("some other sqlite failure")) is False
    assert is_lock_error(RuntimeError("database is locked")) is False


def test_run_with_retry_retries_lock_then_succeeds(monkeypatch):
    calls = {"n": 0}
    sleeps = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    monkeypatch.setattr("app.db.time.sleep", lambda seconds: sleeps.append(seconds))

    result = run_with_retry(flaky, attempts=4, base_delay=0.01, backoff=2.0, label="test")

    assert result == "ok"
    assert calls["n"] == 3
    assert sleeps == [0.01, 0.02]


def test_run_with_retry_does_not_retry_non_lock_errors(monkeypatch):
    sleeps = []

    def broken():
        raise sqlite3.OperationalError("syntax error")

    monkeypatch.setattr("app.db.time.sleep", lambda seconds: sleeps.append(seconds))

    try:
        run_with_retry(broken, attempts=4, base_delay=0.01, backoff=2.0, label="test")
        raise AssertionError("expected exception")
    except sqlite3.OperationalError as exc:
        assert "syntax error" in str(exc)

    assert sleeps == []
