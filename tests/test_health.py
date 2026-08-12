"""Truthful health and ingest-state reporting contracts."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient


class HealthConnection:
    def __init__(self, latest=None, sync=None):
        self.latest = latest
        self.sync = sync

    def execute(self, query, params=()):
        del params
        if "FROM ingest_runs" in query:
            return _Row(self.latest)
        if "FROM sync_state" in query:
            return _Row(self.sync)
        raise AssertionError(f"unexpected health query: {query}")


class _Row:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def _install_health_db(monkeypatch, connection):
    from backend import app as app_module

    @contextmanager
    def connection_context():
        yield connection

    monkeypatch.setattr(app_module.db, "viz_conn", connection_context)
    return app_module


def test_health_reports_empty_database_as_stale_but_operational(monkeypatch):
    app_module = _install_health_db(monkeypatch, HealthConnection())
    monkeypatch.setattr(
        app_module.ingest,
        "progress_snapshot",
        lambda: {"phase": "idle", "last_error": None},
    )

    response = TestClient(app_module.app).get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["db"] is True
    assert body["ingest_running"] is False
    assert body["ingest_progress"] is None
    assert body["last_success"] is None
    assert body["last_error"] is None
    assert body["stale"] is True


def test_health_reports_last_success_and_live_progress(monkeypatch):
    now = datetime.now(timezone.utc)
    latest = (7, now - timedelta(seconds=5), None, "scheduled", None, None, None)
    sync = (
        now - timedelta(seconds=5), now - timedelta(seconds=8),
        now - timedelta(seconds=5), "success", None,
    )
    app_module = _install_health_db(monkeypatch, HealthConnection(latest, sync))
    monkeypatch.setattr(
        app_module.ingest,
        "progress_snapshot",
        lambda: {
            "phase": "committing",
            "done": 2,
            "total": 4,
            "run_id": 7,
            "started_at": (now - timedelta(seconds=5)).isoformat(),
            "last_error": None,
        },
    )

    body = TestClient(app_module.app).get("/health").json()

    assert body["db"] is True
    assert body["ingest_running"] is True
    assert body["ingest_progress"] == {
        "phase": "committing",
        "done": 2,
        "total": 4,
        "pct": 50.0,
        "run_id": 7,
        "started_at": (now - timedelta(seconds=5)).isoformat(),
    }
    assert body["last_success"] == (now - timedelta(seconds=5)).isoformat()
    assert body["last_error"] is None
    assert body["stale"] is False
    assert body["sync_status"] == "success"
    assert body["last_attempt"]["status"] == "success"


def test_health_preserves_last_success_and_exposes_failed_ingest(monkeypatch):
    now = datetime.now(timezone.utc)
    last_success = now - timedelta(hours=3)
    latest = (
        8,
        now - timedelta(minutes=1),
        now - timedelta(minutes=1),
        "manual",
        None,
        None,
        "SourceError: GitHub unavailable",
    )
    sync = (last_success, last_success, now - timedelta(minutes=1), "failure", "SourceError: GitHub unavailable")
    app_module = _install_health_db(monkeypatch, HealthConnection(latest, sync))
    monkeypatch.setattr(
        app_module.ingest,
        "progress_snapshot",
        lambda: {"phase": "idle", "last_error": "SourceError: GitHub unavailable"},
    )
    monkeypatch.setenv("GHPULSE_STALE_AFTER_SECONDS", "7200")

    body = TestClient(app_module.app).get("/health").json()

    assert body["last_success"] == last_success.isoformat()
    assert body["last_error"] == "SourceError: GitHub unavailable"
    assert body["stale"] is True
    assert body["last_ingest"]["id"] == 8
    assert body["last_ingest"]["error"] == "SourceError: GitHub unavailable"
    assert body["sync_status"] == "failure"
    assert body["last_attempt"]["error"] == "SourceError: GitHub unavailable"


def test_health_returns_unhealthy_status_when_database_is_unavailable(monkeypatch):
    from backend import app as app_module

    @contextmanager
    def broken_connection():
        raise ConnectionError("database offline")
        yield  # pragma: no cover

    monkeypatch.setattr(app_module.db, "viz_conn", broken_connection)

    response = TestClient(app_module.app).get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["db"] is False
    assert body["error"] == "database offline"
