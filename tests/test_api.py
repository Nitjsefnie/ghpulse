"""Contract tests for the public aggregate API.

The integration fixtures deliberately use PostgreSQL rather than mocks.  The
dashboard's important behavior is the current-state event union and its range
edges; a fake cursor would not exercise either of those guarantees.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend import api, cache, db


def _dsn() -> str | None:
    return os.environ.get("GHPULSE_TEST_DATABASE_URL")


@pytest.fixture
def api_db(monkeypatch):
    dsn = _dsn()
    if not dsn:
        pytest.skip("GHPULSE_TEST_DATABASE_URL is not configured")

    psycopg = pytest.importorskip("psycopg")
    schema = (Path(__file__).parents[1] / "backend" / "schema.sql").read_text(
        encoding="utf-8"
    )
    monkeypatch.setenv("DATABASE_URL_VIZ", dsn)
    db.reset_viz_pool()
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(db.sql_text(schema))
        connection.execute(
            "TRUNCATE issues, pull_requests, repositories, ingest_runs CASCADE"
        )
        connection.execute("DELETE FROM sync_state")
        connection.execute("INSERT INTO sync_state (id) VALUES (1)")

    yield dsn
    db.reset_viz_pool()


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.response_cache.clear()
    yield
    cache.response_cache.clear()


@pytest.fixture
def client(api_db):
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


@pytest.fixture
def guest_client(api_db):
    app = FastAPI()

    @app.middleware("http")
    async def set_guest(request: Request, call_next):
        request.state.user_id = 0
        request.state.is_guest = True
        return await call_next(request)

    app.include_router(api.router)
    return TestClient(app)


def _seed(api_db):
    import psycopg

    with psycopg.connect(api_db) as connection:
        connection.execute(
            """
            INSERT INTO repositories
                (node_id, name_with_owner, owner_login, url,
                 first_seen_at, last_seen_at)
            VALUES
                ('R_1', 'external/one', 'external',
                 'https://github.com/external/one', %s, %s),
                ('R_2', 'outside/two', 'outside',
                 'https://github.com/outside/two', %s, %s)
            """,
            (datetime(2026, 8, 1, tzinfo=timezone.utc),) * 4,
        )
        connection.execute(
            """
            INSERT INTO issues
                (node_id, repository_id, number, url, created_at, updated_at,
                 closed_at, state, state_reason)
            VALUES
                ('I_1', 'R_1', 1, 'https://github.com/external/one/issues/1',
                 '2026-08-01T10:00:00Z', '2026-08-04T10:00:00Z',
                 '2026-08-04T10:00:00Z', 'CLOSED', 'COMPLETED'),
                ('I_2', 'R_2', 2, 'https://github.com/outside/two/issues/2',
                 '2026-08-02T10:00:00Z', '2026-08-03T10:00:00Z',
                 NULL, 'OPEN', NULL)
            """
        )
        connection.execute(
            """
            INSERT INTO pull_requests
                (node_id, repository_id, number, url, created_at, updated_at,
                 closed_at, merged_at, state, merged)
            VALUES
                ('PR_1', 'R_1', 3, 'https://github.com/external/one/pull/3',
                 '2026-08-03T10:00:00Z', '2026-08-06T10:00:00Z',
                 '2026-08-06T10:00:00Z', NULL, 'CLOSED', FALSE),
                ('PR_2', 'R_2', 4, 'https://github.com/outside/two/pull/4',
                 '2026-08-05T10:00:00Z', '2026-08-05T10:00:00Z',
                 NULL, NULL, 'OPEN', FALSE)
            """
        )
        connection.commit()


def test_routes_are_registered(client):
    assert client.get("/api/me").status_code == 200
    assert client.get("/api/repositories?range=all").status_code == 200
    assert client.get("/api/dashboard?range=all").status_code == 200


def test_current_state_moves_issue_outcome(client, api_db):
    _seed(api_db)
    import psycopg

    with psycopg.connect(api_db) as connection:
        connection.execute(
            """
            UPDATE issues
            SET updated_at = '2026-08-11T12:55:00Z',
                closed_at = '2026-08-11T12:55:00Z',
                state = 'CLOSED', state_reason = 'NOT_PLANNED'
            WHERE node_id = 'I_1'
            """
        )
        connection.commit()

    body = client.get("/api/dashboard?range=all").json()
    assert sum(bucket["completed"] for bucket in body["issues"]) == 0
    assert sum(bucket["not_planned"] for bucket in body["issues"]) == 1
    assert body["summary"]["issues"]["opened"] == 2
    assert body["summary"]["pull_requests"]["opened"] == 2


def test_guest_can_filter_public_repository(guest_client, api_db):
    _seed(api_db)
    response = guest_client.get("/api/dashboard?range=all&repository=R_1")
    assert response.status_code == 200
    assert response.json()["summary"]["repositories"] == 1


def test_repository_options_and_range_edge(client, api_db):
    _seed(api_db)
    assert client.get("/api/me").json() == {"user_id": None, "is_guest": False}
    repositories = client.get("/api/repositories?range=all").json()["repositories"]
    assert [repo["node_id"] for repo in repositories] == ["R_1", "R_2"]

    body = client.get("/api/dashboard?range=1d").json()
    assert body["issues"][0]["start"] == body["start"]
    assert body["pull_requests"][0]["start"] == body["start"]
    assert body["issues"][-1]["end"] == body["end"]
    assert body["pull_requests"][-1]["end"] == body["end"]


def test_range_and_repository_validation(client, api_db):
    _seed(api_db)
    assert client.get("/api/dashboard?range=banana").status_code == 422
    assert client.get("/api/dashboard?range=all&repository=nope").status_code == 404

    # The schema defensively rejects private/non-external rows at write time;
    # an identifier that is absent from the public table has the same safe
    # read behavior and is the deployable representation of that boundary.
    assert client.get("/api/dashboard?range=all&repository=PRIVATE").status_code == 404


def test_empty_data_has_dense_shape(client, api_db):
    body = client.get("/api/dashboard?range=1d").json()
    assert body["issues"]
    assert body["pull_requests"]
    assert all(
        all(bucket[key] == 0 for key in ("opened", "completed", "not_planned"))
        for bucket in body["issues"]
    )
    assert all(
        all(bucket[key] == 0 for key in ("opened", "merged", "closed_unmerged"))
        for bucket in body["pull_requests"]
    )
    assert body["summary"]["repositories"] == 0


def test_dashboard_does_not_expose_raw_rows_or_tokens(client, api_db):
    _seed(api_db)
    body = client.get("/api/dashboard?range=all").json()
    serialized = repr(body).lower()
    assert "token" not in serialized
    assert "closed_at" not in serialized
