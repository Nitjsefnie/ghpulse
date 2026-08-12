"""Lifecycle, static-shell, and production middleware contracts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import session


ROOT = Path(__file__).resolve().parents[1]


class FakeScheduler:
    """Small scheduler seam which records the production job contract."""

    instances: list["FakeScheduler"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.jobs: list[dict] = []
        self.started = False
        self.shutdown_wait: bool | None = None
        self.__class__.instances.append(self)

    def add_job(self, func, trigger, **kwargs):
        job = {"func": func, "trigger": trigger, **kwargs}
        self.jobs.append(job)
        return job

    def start(self):
        self.started = True

    def shutdown(self, *, wait=True):
        self.shutdown_wait = wait


class FakePool:
    def __init__(self):
        self.open_calls: list[dict] = []
        self.closed = False

    def open(self, **kwargs):
        self.open_calls.append(kwargs)

    def close(self, **kwargs):
        del kwargs
        self.closed = True


@pytest.fixture
def isolated_app(monkeypatch):
    """Make the real app lifecycle deterministic without a PostgreSQL server."""
    from backend import app as app_module

    viz_pool = FakePool()
    auth_pool = FakePool()
    scheduled_calls: list[str] = []
    close_calls: list[bool] = []
    cache_lifecycle: list[str] = []

    def open_pools(*, wait=True):
        viz_pool.open(wait=wait)
        auth_pool.open(wait=wait)
        return viz_pool, auth_pool

    monkeypatch.setattr(app_module.db, "open_pools", open_pools)
    monkeypatch.setattr(app_module.db, "close_pools", lambda: close_calls.append(True))
    monkeypatch.setattr(app_module.db, "schema_check", lambda: None)
    monkeypatch.setattr(
        app_module.cache, "start_refresh_workers", lambda: cache_lifecycle.append("start")
    )
    monkeypatch.setattr(
        app_module.cache, "stop_refresh_workers", lambda: cache_lifecycle.append("stop")
    )
    monkeypatch.setattr(app_module, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(
        app_module.ingest,
        "run_ingest",
        lambda trigger: scheduled_calls.append(trigger) or {"skipped": False},
    )
    FakeScheduler.instances.clear()
    return (
        app_module.app, app_module, scheduled_calls, close_calls,
        viz_pool, auth_pool, cache_lifecycle,
    )


def test_lifespan_opens_pools_and_schedules_complete_startup_and_hourly_ingest(
    isolated_app,
):
    app, app_module, scheduled_calls, close_calls, viz_pool, auth_pool, cache_lifecycle = (
        isolated_app
    )

    with TestClient(app):
        scheduler = FakeScheduler.instances[-1]
        assert scheduler.started
        assert viz_pool.open_calls == [{"wait": True}]
        assert auth_pool.open_calls == [{"wait": True}]
        assert {job["trigger"] for job in scheduler.jobs} == {"date", "interval"}
        interval = next(job for job in scheduler.jobs if job["trigger"] == "interval")
        assert interval["hours"] == 1
        startup = next(job for job in scheduler.jobs if job["trigger"] == "date")
        startup["func"]()
        interval["func"]()

    assert scheduled_calls == ["startup", "scheduled"]
    assert scheduler.shutdown_wait is False
    assert app_module.events.shutdown_event() is None
    assert close_calls == [True]
    assert cache_lifecycle == ["start", "stop"]


@pytest.mark.asyncio
async def test_real_scheduler_drains_blocked_ingest_before_closing_pools(
    monkeypatch,
):
    """A running APScheduler worker must finish before lifespan teardown closes DB."""
    from apscheduler.events import EVENT_JOB_ERROR
    from apscheduler.schedulers.background import BackgroundScheduler as RealScheduler

    from backend import app as app_module

    started = threading.Event()
    release = threading.Event()
    closed = threading.Event()
    order: list[str] = []
    scheduler_holder = []

    class RecordingScheduler(RealScheduler):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.job_errors = []
            self.add_listener(self.job_errors.append, EVENT_JOB_ERROR)
            scheduler_holder.append(self)

    class Pool:
        closed = False

        def open(self, **kwargs):
            del kwargs

        def close(self, **kwargs):
            del kwargs
            order.append("pools-closed")
            closed.set()

    pool = Pool()

    def open_pools(*, wait=True):
        del wait
        return pool, pool

    def blocked_ingest(trigger):
        order.append(f"started:{trigger}")
        started.set()
        while not release.wait(0.01):
            if closed.is_set():
                raise AssertionError("pool closed while ingest was still running")
        order.append(f"finished:{trigger}")
        return {"skipped": False}

    monkeypatch.setattr(app_module.db, "open_pools", open_pools)
    monkeypatch.setattr(app_module.db, "close_pools", pool.close)
    monkeypatch.setattr(app_module.db, "schema_check", lambda: None)
    monkeypatch.setattr(app_module, "BackgroundScheduler", RecordingScheduler)
    monkeypatch.setattr(app_module.ingest, "run_ingest", blocked_ingest)

    context = app_module.lifespan(app_module.app)
    await context.__aenter__()
    assert await asyncio.to_thread(started.wait, 2)

    shutdown = asyncio.create_task(context.__aexit__(None, None, None))
    await asyncio.sleep(0.05)
    assert not shutdown.done()
    assert not closed.is_set()

    release.set()
    await asyncio.wait_for(shutdown, timeout=2)
    assert closed.is_set()
    assert order.index("finished:startup") < order.index("pools-closed")
    assert scheduler_holder[-1].job_errors == []


def test_guest_can_load_static_shell_with_safe_session_injection_and_hashes(
    isolated_app, monkeypatch
):
    app, _app_module, _calls, _close_calls, _viz_pool, _auth_pool, _cache_lifecycle = isolated_app
    token = "server-only-github-token"
    monkeypatch.setenv("GH_TOKEN", token)
    monkeypatch.setenv("BACKEND_URL", "/dashboard")

    client = TestClient(app)
    client.cookies.set(session.SESSION_COOKIE_NAME, session.make_guest_session_token())
    response = client.get("/")

    assert response.status_code == 200
    body = response.text
    assert 'window.BACKEND_URL = "/dashboard"' in body
    assert "window.IS_GUEST = true" in body
    assert token not in body
    assert "react.production.min.js" in body
    assert body.index("react.production.min.js") < body.index("babel.min.js")
    assert body.index("/src/dashboard-charts.jsx?v=") < body.index("/src/app.jsx?v=")

    css_hash = hashlib.sha256((ROOT / "public" / "app.css").read_bytes()).hexdigest()
    assert f"/app.css?v={css_hash}" in body
    assert "content-security-policy" in {key.lower() for key in response.headers}
    assert "unsafe-eval" in response.headers["content-security-policy"]


def test_admin_ingest_requires_constant_time_token_and_same_origin(
    isolated_app, monkeypatch
):
    (
        app, app_module, scheduled_calls, _close_calls,
        _viz_pool, _auth_pool, _cache_lifecycle,
    ) = isolated_app
    monkeypatch.setenv("ADMIN_TOKEN", "correct-token")
    compare_calls: list[tuple[str, str]] = []
    real_compare = session.hmac.compare_digest

    def tracked_compare(left, right):
        compare_calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(session.hmac, "compare_digest", tracked_compare)
    client = TestClient(app)

    assert client.post("/admin/ingest").status_code == 401
    assert client.post(
        "/admin/ingest",
        headers={"X-Admin-Token": "correct-token", "Origin": "https://evil.example"},
    ).status_code == 403

    monkeypatch.setattr(
        app_module.ingest,
        "run_ingest",
        lambda trigger: scheduled_calls.append(trigger) or {"skipped": False},
    )
    response = client.post(
        "/admin/ingest",
        headers={"X-Admin-Token": "correct-token", "Origin": "http://testserver"},
    )
    assert response.status_code == 200
    assert response.json()["skipped"] is False
    assert scheduled_calls[-1] == "manual"
    assert compare_calls
    assert any(
        left == "correct-token" and right == "correct-token"
        for left, right in compare_calls
    )


def test_authenticated_and_guest_api_paths_use_production_middleware(
    isolated_app, monkeypatch
):
    app, app_module, _calls, _close_calls, _viz_pool, _auth_pool, _cache_lifecycle = isolated_app
    client = TestClient(app)

    assert client.get("/api/me").status_code == 401
    client.cookies.set(session.SESSION_COOKIE_NAME, session.make_guest_session_token())
    guest = client.get("/api/me")
    assert guest.status_code == 200
    assert guest.json() == {"user_id": 0, "is_guest": True}

    monkeypatch.setattr(app_module.session, "resolve_session_user_id", lambda token: 42)
    client.cookies.set(session.SESSION_COOKIE_NAME, "authenticated-token")
    authenticated = client.get("/api/me")
    assert authenticated.status_code == 200
    assert authenticated.json() == {"user_id": 42, "is_guest": False}


@pytest.mark.asyncio
async def test_sse_route_shutdown_stops_an_active_stream(isolated_app):
    _app, app_module, _calls, _close_calls, _viz_pool, _auth_pool, _cache_lifecycle = isolated_app
    from backend import events

    class ConnectedRequest:
        async def is_disconnected(self):
            return False

    events.set_loop(asyncio.get_running_loop())
    response = await app_module._event_stream(ConnectedRequest())
    stream = response.body_iterator
    assert await stream.__anext__() == ": connected\n\n"
    events.signal_shutdown()
    await asyncio.sleep(0)
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(stream.__anext__(), timeout=1)
    await stream.aclose()
    events.clear_loop()


@pytest.mark.asyncio
async def test_sse_route_cancellation_cleans_waiter_tasks(isolated_app):
    """Client navigation must not leave queue/event waiters on the loop."""
    _app, app_module, _calls, _close_calls, _viz_pool, _auth_pool, _cache_lifecycle = isolated_app
    from backend import events

    class ConnectedRequest:
        async def is_disconnected(self):
            return False

    events.set_loop(asyncio.get_running_loop())
    response = await app_module._event_stream(ConnectedRequest())
    stream = response.body_iterator
    assert await stream.__anext__() == ": connected\n\n"

    read = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)
    read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await read
    await stream.aclose()

    current = asyncio.current_task()
    leaked = [
        task for task in asyncio.all_tasks()
        if task is not current
        and not task.done()
        and getattr(task.get_coro(), "__qualname__", "") in {"Queue.get", "Event.wait"}
    ]
    assert leaked == []
    events.clear_loop()


def test_served_index_keeps_json_injection_valid_for_quoted_backend_url(
    isolated_app, monkeypatch
):
    app, _app_module, _calls, _close_calls, _viz_pool, _auth_pool, _cache_lifecycle = isolated_app
    monkeypatch.setenv("BACKEND_URL", '/api?x="unsafe"&y=</script>')
    client = TestClient(app)
    client.cookies.set(session.SESSION_COOKIE_NAME, session.make_guest_session_token())
    body = client.get("/").text
    marker = "window.BACKEND_URL = "
    start = body.index(marker) + len(marker)
    end = body.index("; window.IS_GUEST", start)
    assert json.loads(body[start:end]) == '/api?x="unsafe"&y=</script>'


def test_external_backend_url_is_allowed_by_connect_policy(isolated_app, monkeypatch):
    app, _app_module, _calls, _close_calls, _viz_pool, _auth_pool, _cache_lifecycle = isolated_app
    monkeypatch.setenv("BACKEND_URL", "https://api.example.test/v1/")
    client = TestClient(app)
    client.cookies.set(session.SESSION_COOKIE_NAME, session.make_guest_session_token())

    response = client.get("/")

    assert response.status_code == 200
    assert "connect-src 'self' https://api.example.test;" in response.headers[
        "content-security-policy"
    ]
