"""Tests for the PostgreSQL current-state persistence schema."""

from __future__ import annotations

import os
from pathlib import Path
import re

import pytest


SCHEMA = Path(__file__).parents[1] / "backend" / "schema.sql"


@pytest.fixture
def schema_text() -> str:
    return SCHEMA.read_text(encoding="utf-8")


def test_schema_declares_only_current_state_item_tables(schema_text):
    assert "CREATE TABLE IF NOT EXISTS repositories" in schema_text
    assert "CREATE TABLE IF NOT EXISTS issues" in schema_text
    assert "CREATE TABLE IF NOT EXISTS pull_requests" in schema_text
    assert "state_reason" in schema_text
    assert "issue_events" not in schema_text
    assert "pull_request_events" not in schema_text
    assert "membership" not in schema_text.lower()
    assert "insider" not in schema_text.lower()


def test_schema_uses_node_ids_and_cascading_repository_foreign_keys(schema_text):
    assert re.search(r"node_id\s+TEXT PRIMARY KEY", schema_text)
    assert "REFERENCES repositories(node_id) ON DELETE CASCADE" in schema_text
    assert schema_text.count("REFERENCES repositories(node_id) ON DELETE CASCADE") == 2


def test_schema_indexes_all_dashboard_timestamp_dimensions(schema_text):
    required_indexes = {
        "issues_repository_idx": ("issues", "repository_id"),
        "issues_created_at_idx": ("issues", "created_at"),
        "issues_updated_at_idx": ("issues", "updated_at"),
        "issues_closed_at_idx": ("issues", "closed_at"),
        "pull_requests_repository_idx": ("pull_requests", "repository_id"),
        "pull_requests_created_at_idx": ("pull_requests", "created_at"),
        "pull_requests_updated_at_idx": ("pull_requests", "updated_at"),
        "pull_requests_closed_at_idx": ("pull_requests", "closed_at"),
        "pull_requests_merged_at_idx": ("pull_requests", "merged_at"),
    }
    for index_name, (table_name, column_name) in required_indexes.items():
        pattern = (
            rf"CREATE INDEX IF NOT EXISTS {index_name}\s+"
            rf"ON {table_name}\s*\(\s*{column_name}\s*\)"
        )
        assert re.search(pattern, schema_text), index_name


def test_schema_has_ingest_audit_and_singleton_sync_state(schema_text):
    assert "CREATE TABLE IF NOT EXISTS ingest_runs" in schema_text
    assert "fetched" in schema_text
    assert "upserted" in schema_text
    assert "deleted" in schema_text
    assert "started_at" in schema_text
    assert "finished_at" in schema_text
    assert "error" in schema_text
    assert "CREATE TABLE IF NOT EXISTS sync_state" in schema_text
    assert "last_committed_at" in schema_text
    assert "last_source_snapshot_at" in schema_text
    assert "last_attempt_at" in schema_text
    assert "last_attempt_status" in schema_text
    assert "last_attempt_error" in schema_text
    assert "data_changed" in schema_text
    assert "CHECK (id = 1)" in schema_text


def test_schema_applies_twice_when_test_database_is_configured(schema_text):
    """Exercise PostgreSQL idempotency when the caller supplies a disposable DSN.

    CI can set ``GHPULSE_TEST_DATABASE_URL`` to a disposable database. Local
    environments without PostgreSQL are covered by the structural tests above
    and the task report records that limitation.
    """
    dsn = os.environ.get("GHPULSE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("GHPULSE_TEST_DATABASE_URL is not configured")

    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(schema_text)
            cursor.execute(schema_text)
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name IN "
                "('repositories', 'issues', 'pull_requests', 'ingest_runs', 'sync_state') "
                "ORDER BY table_name"
            )
            assert [row[0] for row in cursor.fetchall()] == [
                "ingest_runs",
                "issues",
                "pull_requests",
                "repositories",
                "sync_state",
            ]
            cursor.execute(
                "INSERT INTO repositories "
                "(node_id, name_with_owner, owner_login, url, first_seen_at, last_seen_at) "
                "VALUES ('R_test', 'external/project', 'external', "
                "'https://github.com/external/project', now(), now())"
            )
            cursor.execute(
                "INSERT INTO issues "
                "(node_id, repository_id, number, url, created_at, updated_at, state) "
                "VALUES ('I_test', 'R_test', 1, "
                "'https://github.com/external/project/issues/1', now(), now(), 'OPEN')"
            )
            cursor.execute("DELETE FROM repositories WHERE node_id = 'R_test'")
            cursor.execute("SELECT count(*) FROM issues WHERE node_id = 'I_test'")
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT count(*) FROM sync_state")
            assert cursor.fetchone()[0] == 1


_TASK2_OLD_SCHEMA = """
CREATE TABLE repositories (
  node_id TEXT PRIMARY KEY,
  name_with_owner TEXT NOT NULL,
  owner_login TEXT NOT NULL,
  url TEXT NOT NULL,
  is_private BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_private IS FALSE),
  is_external BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_external IS TRUE),
  first_seen_at TIMESTAMPTZ NOT NULL,
  last_seen_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE issues (
  node_id TEXT PRIMARY KEY,
  repository_id TEXT NOT NULL REFERENCES repositories(node_id) ON DELETE CASCADE,
  number INTEGER NOT NULL CHECK (number > 0),
  url TEXT NOT NULL,
  is_private BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_private IS FALSE),
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  closed_at TIMESTAMPTZ,
  state TEXT NOT NULL CHECK (state IN ('OPEN', 'CLOSED')),
  state_reason TEXT CHECK (state_reason IS NULL OR state_reason IN
                           ('COMPLETED', 'NOT_PLANNED', 'REOPENED')),
  CHECK ((state = 'OPEN' AND closed_at IS NULL
          AND (state_reason IS NULL OR state_reason = 'REOPENED'))
         OR (state = 'CLOSED' AND closed_at IS NOT NULL
             AND state_reason IN ('COMPLETED', 'NOT_PLANNED')))
);
CREATE TABLE pull_requests (
  node_id TEXT PRIMARY KEY,
  repository_id TEXT NOT NULL REFERENCES repositories(node_id) ON DELETE CASCADE,
  number INTEGER NOT NULL CHECK (number > 0),
  url TEXT NOT NULL,
  is_private BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_private IS FALSE),
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  closed_at TIMESTAMPTZ,
  merged_at TIMESTAMPTZ,
  state TEXT NOT NULL CHECK (state IN ('OPEN', 'CLOSED', 'MERGED')),
  merged BOOLEAN NOT NULL DEFAULT FALSE,
  CHECK (state = 'OPEN' AND merged IS FALSE AND closed_at IS NULL AND merged_at IS NULL
         OR state = 'CLOSED' AND merged IS FALSE AND closed_at IS NOT NULL
         OR state = 'MERGED' AND merged IS TRUE AND closed_at IS NOT NULL
                         AND merged_at IS NOT NULL)
);
CREATE TABLE ingest_runs (
  id BIGSERIAL PRIMARY KEY,
  trigger TEXT NOT NULL,
  full_sync BOOLEAN NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  repositories_fetched INTEGER NOT NULL DEFAULT 0,
  issues_fetched INTEGER NOT NULL DEFAULT 0,
  pull_requests_fetched INTEGER NOT NULL DEFAULT 0,
  repositories_upserted INTEGER NOT NULL DEFAULT 0,
  issues_upserted INTEGER NOT NULL DEFAULT 0,
  pull_requests_upserted INTEGER NOT NULL DEFAULT 0,
  repositories_deleted INTEGER NOT NULL DEFAULT 0,
  issues_deleted INTEGER NOT NULL DEFAULT 0,
  pull_requests_deleted INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
CREATE TABLE sync_state (
  id SMALLINT PRIMARY KEY CHECK (id = 1),
  last_successful_high_water TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO sync_state (id) VALUES (1);
"""


def test_schema_upgrades_task2_audit_shape_and_removes_obsolete_columns(schema_text):
    """A disposable PostgreSQL database must upgrade, then reapply cleanly."""
    dsn = os.environ.get("GHPULSE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("GHPULSE_TEST_DATABASE_URL is not configured")

    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            "DROP TABLE IF EXISTS issues, pull_requests, repositories, "
            "ingest_runs, sync_state CASCADE"
        )
        connection.execute(_TASK2_OLD_SCHEMA)
        connection.execute(schema_text)
        connection.execute(schema_text)

        columns = {
            table: {
                row[0]
                for row in connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s",
                    (table,),
                ).fetchall()
            }
            for table in ("ingest_runs", "sync_state", "repositories", "issues", "pull_requests")
        }
        assert {"committed_at", "source_snapshot_at", "data_changed"} <= columns[
            "ingest_runs"
        ]
        assert {"last_committed_at", "last_source_snapshot_at", "last_attempt_at",
                "last_attempt_status", "last_attempt_error"} <= columns[
            "sync_state"
        ]
        assert "full_sync" not in columns["ingest_runs"]
        assert "last_successful_high_water" not in columns["sync_state"]
        assert {"node_id", "is_external", "first_seen_at", "last_seen_at"} <= columns[
            "repositories"
        ]
        assert {"state", "state_reason", "closed_at"} <= columns["issues"]
        assert {"state", "merged", "merged_at"} <= columns["pull_requests"]

        constraints = connection.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'issues'::regclass AND contype = 'c'"
        ).fetchall()
        assert any("state_reason" in row[0] for row in constraints)
        foreign_keys = connection.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid IN ('issues'::regclass, 'pull_requests'::regclass) "
            "AND contype = 'f'"
        ).fetchall()
        assert len(foreign_keys) == 2
        assert all("ON DELETE CASCADE" in row[0] for row in foreign_keys)
