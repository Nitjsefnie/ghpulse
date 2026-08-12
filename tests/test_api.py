"""Contract tests for the public aggregate API.

The integration fixtures deliberately use PostgreSQL rather than mocks.  The
dashboard's important behavior is the current-state event union and its range
edges; a fake cursor would not exercise either of those guarantees.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend import api, api_common, api_dashboard, cache, db


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


def _seed_repository(api_db, node_id="R_BOUNDARY"):
    import psycopg

    with psycopg.connect(api_db) as connection:
        connection.execute(
            """
            INSERT INTO repositories
                (node_id, name_with_owner, owner_login, url,
                 first_seen_at, last_seen_at)
            VALUES (%s, %s, 'external', %s, %s, %s)
            """,
            (
                node_id,
                f"external/{node_id.lower()}",
                f"https://github.com/external/{node_id.lower()}",
                datetime(2026, 8, 1, tzinfo=timezone.utc),
                datetime(2026, 8, 1, tzinfo=timezone.utc),
            ),
        )
        connection.commit()


def _insert_issue(
    api_db,
    node_id,
    repository_id,
    created_at,
    *,
    closed_at=None,
    state="OPEN",
    state_reason=None,
):
    import psycopg

    with psycopg.connect(api_db) as connection:
        connection.execute(
            """
            INSERT INTO issues
                (node_id, repository_id, number, url, created_at, updated_at,
                 closed_at, state, state_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                node_id,
                repository_id,
                int(node_id.split("_")[-1]),
                f"https://github.com/external/{repository_id.lower()}/issues/1",
                created_at,
                created_at,
                closed_at,
                state,
                state_reason,
            ),
        )
        connection.commit()


def _insert_pull_request(
    api_db,
    node_id,
    repository_id,
    created_at,
    *,
    closed_at=None,
    merged_at=None,
    state="OPEN",
    merged=False,
):
    import psycopg

    with psycopg.connect(api_db) as connection:
        connection.execute(
            """
            INSERT INTO pull_requests
                (node_id, repository_id, number, url, created_at, updated_at,
                 closed_at, merged_at, state, merged)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                node_id,
                repository_id,
                int(node_id.split("_")[-1]),
                f"https://github.com/external/{repository_id.lower()}/pull/1",
                created_at,
                created_at,
                closed_at,
                merged_at,
                state,
                merged,
            ),
        )
        connection.commit()


def test_routes_are_registered(client):
    assert client.get("/api/me").status_code == 200
    assert client.get("/api/repositories?range=all").status_code == 200
    assert client.get("/api/dashboard?range=all").status_code == 200


def test_dashboard_overlays_uncached_sync_status_without_recomputing_aggregate(
    guest_client, api_db
):
    _seed(api_db)
    import psycopg

    with psycopg.connect(api_db) as connection:
        connection.execute(
            "UPDATE sync_state SET last_attempt_at = %s, last_attempt_status = 'success', "
            "last_attempt_error = NULL WHERE id = 1",
            ("2026-08-12T10:00:00Z",),
        )
        connection.commit()

    first = guest_client.get("/api/dashboard?range=all").json()
    assert first["summary"]["sync_status"] == "success"
    assert first["summary"]["sync_last_attempt_at"].startswith("2026-08-12T10:00:00")

    with psycopg.connect(api_db) as connection:
        connection.execute(
            "UPDATE sync_state SET last_attempt_at = %s, last_attempt_status = 'failure', "
            "last_attempt_error = %s WHERE id = 1",
            ("2026-08-12T10:01:00Z", "sentinel-sync-secret"),
        )
        connection.commit()

    failed = guest_client.get("/api/dashboard?range=all").json()
    assert failed["issues"] == first["issues"]
    assert failed["pull_requests"] == first["pull_requests"]
    assert failed["summary"]["sync_status"] == "failure"
    assert failed["summary"]["sync_error_code"] == "SYNC_FAILED"
    assert failed["summary"]["sync_error"] == "sync failed"
    assert "sentinel-sync-secret" not in str(failed)

    with psycopg.connect(api_db) as connection:
        connection.execute(
            "UPDATE sync_state SET last_attempt_at = %s, last_attempt_status = 'success', "
            "last_attempt_error = NULL WHERE id = 1",
            ("2026-08-12T10:02:00Z",),
        )
        connection.commit()

    success = guest_client.get("/api/dashboard?range=all").json()
    assert success["summary"]["sync_status"] == "success"
    assert success["summary"]["sync_last_attempt_at"].startswith("2026-08-12T10:02:00")
    assert success["summary"]["sync_error"] is None


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


def test_reopened_issue_removes_current_outcome(client, api_db):
    _seed(api_db)
    before = client.get("/api/dashboard?range=all&fresh=1").json()
    assert sum(bucket["completed"] for bucket in before["issues"]) == 1

    import psycopg

    with psycopg.connect(api_db) as connection:
        connection.execute(
            """
            UPDATE issues
            SET updated_at = '2026-08-11T13:00:00Z', closed_at = NULL,
                state = 'OPEN', state_reason = NULL
            WHERE node_id = 'I_1'
            """
        )
        connection.commit()

    after = client.get("/api/dashboard?range=all&fresh=1").json()
    assert sum(bucket["completed"] for bucket in after["issues"]) == 0
    assert sum(bucket["not_planned"] for bucket in after["issues"]) == 0


def test_pull_request_outcomes_follow_current_state(client, api_db):
    _seed(api_db)
    before = client.get("/api/dashboard?range=all&fresh=1").json()
    assert sum(bucket["closed_unmerged"] for bucket in before["pull_requests"]) == 1

    import psycopg

    with psycopg.connect(api_db) as connection:
        connection.execute(
            """
            UPDATE pull_requests
            SET updated_at = '2026-08-11T13:00:00Z',
                closed_at = '2026-08-11T13:00:00Z',
                merged_at = '2026-08-11T13:00:00Z',
                state = 'MERGED', merged = TRUE
            WHERE node_id = 'PR_1'
            """
        )
        connection.commit()

    merged = client.get("/api/dashboard?range=all&fresh=1").json()
    assert sum(bucket["closed_unmerged"] for bucket in merged["pull_requests"]) == 0
    assert sum(bucket["merged"] for bucket in merged["pull_requests"]) == 1

    with psycopg.connect(api_db) as connection:
        connection.execute(
            """
            UPDATE pull_requests
            SET updated_at = '2026-08-11T14:00:00Z',
                closed_at = NULL, merged_at = NULL,
                state = 'OPEN', merged = FALSE
            WHERE node_id = 'PR_1'
            """
        )
        connection.commit()

    reopened = client.get("/api/dashboard?range=all&fresh=1").json()
    assert sum(bucket["closed_unmerged"] for bucket in reopened["pull_requests"]) == 0
    assert sum(bucket["merged"] for bucket in reopened["pull_requests"]) == 0


def test_repository_filter_scopes_both_event_panels(client, api_db):
    _seed(api_db)
    body = client.get("/api/dashboard?range=all&repository=R_1&fresh=1").json()
    assert body["summary"]["repositories"] == 1
    assert body["summary"]["issues"]["opened"] == 1
    assert body["summary"]["issues"]["completed"] == 1
    assert body["summary"]["pull_requests"]["opened"] == 1
    assert body["summary"]["pull_requests"]["closed_unmerged"] == 1
    assert sum(bucket["opened"] for bucket in body["issues"]) == 1
    assert sum(bucket["opened"] for bucket in body["pull_requests"]) == 1


def test_dashboard_uses_repeatable_read_for_aggregate_and_status_overlay(
    client, api_db, monkeypatch
):
    _seed(api_db)
    real_viz_conn = db.viz_conn
    connections = []

    @contextmanager
    def tracked_viz_conn():
        with real_viz_conn() as connection:
            connections.append(connection)
            yield connection

    monkeypatch.setattr(db, "viz_conn", tracked_viz_conn)
    response = client.get("/api/dashboard?range=all&fresh=1")
    assert response.status_code == 200
    # The aggregate is one repeatable-read snapshot; durable sync status is a
    # separate uncached read so a cached aggregate cannot hide a later failure.
    assert len(connections) == 2


def test_read_transaction_is_repeatable_read_and_read_only(api_db):
    with api_common.read_transaction() as connection:
        row = connection.execute(
            "SELECT current_setting('transaction_isolation'), "
            "current_setting('transaction_read_only')"
        ).fetchone()
    assert row == ("repeatable read", "on")


def test_event_range_is_half_open_at_exact_edges(client, api_db, monkeypatch):
    fixed_now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(api_dashboard, "utc_now", lambda: fixed_now)
    _seed_repository(api_db)
    start = fixed_now - timedelta(days=1)

    _insert_issue(
        api_db,
        "I_101",
        "R_BOUNDARY",
        start,
        closed_at=start,
        state="CLOSED",
        state_reason="COMPLETED",
    )
    _insert_issue(api_db, "I_102", "R_BOUNDARY", start + timedelta(hours=1))
    _insert_issue(api_db, "I_103", "R_BOUNDARY", fixed_now)
    _insert_pull_request(api_db, "PR_101", "R_BOUNDARY", start)
    _insert_pull_request(api_db, "PR_102", "R_BOUNDARY", fixed_now)

    body = client.get("/api/dashboard?range=1d&fresh=1").json()
    assert body["start"] == start.isoformat()
    assert body["end"] == fixed_now.isoformat()
    assert body["summary"]["issues"]["opened"] == 2
    assert body["summary"]["issues"]["completed"] == 1
    assert body["summary"]["pull_requests"]["opened"] == 1


@pytest.mark.parametrize(
    ("range_value", "expected_bucket_s"),
    [("1h", 60), ("1d", 300), ("7d", 3600), ("30d", 21600), ("90d", 43200)],
)
def test_adaptive_bucket_widths(range_value, expected_bucket_s):
    assert api_common._bucket_seconds(api_common._parse_range(range_value)) == expected_bucket_s


def test_current_open_summary_only_counts_rows_created_in_range(client, api_db, monkeypatch):
    fixed_now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(api_dashboard, "utc_now", lambda: fixed_now)
    _seed_repository(api_db, "R_OPEN")
    start = fixed_now - timedelta(days=7)

    _insert_issue(api_db, "I_201", "R_OPEN", start + timedelta(days=1))
    _insert_issue(api_db, "I_202", "R_OPEN", start - timedelta(days=1))
    _insert_pull_request(api_db, "PR_201", "R_OPEN", start + timedelta(days=1))
    _insert_pull_request(api_db, "PR_202", "R_OPEN", start - timedelta(days=1))

    body = client.get("/api/dashboard?range=7d&fresh=1").json()
    assert body["summary"]["issues"]["currently_open"] == 1
    assert body["summary"]["pull_requests"]["currently_open"] == 1


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
