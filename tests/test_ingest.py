"""Integration and seam tests for the complete current-state ingest."""

from __future__ import annotations

import copy
from datetime import timezone
import json
import os
from pathlib import Path
import threading

import pytest

from backend import db


FIXTURES = Path(__file__).parent / "fixtures"


def _snapshot(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def ingest_db(monkeypatch):
    """Use a disposable PostgreSQL database when the caller supplies one."""
    dsn = os.environ.get("GHPULSE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("GHPULSE_TEST_DATABASE_URL is not configured")

    psycopg = pytest.importorskip("psycopg")
    schema = (Path(__file__).parents[1] / "backend" / "schema.sql").read_text(
        encoding="utf-8"
    )
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(db.sql_text(schema))
        connection.execute(
            "TRUNCATE issues, pull_requests, repositories, ingest_runs CASCADE"
        )
        connection.execute("DELETE FROM sync_state")
        connection.execute("INSERT INTO sync_state (id) VALUES (1)")

    monkeypatch.setenv("DATABASE_URL_VIZ", dsn)
    db.reset_viz_pool()
    yield dsn
    db.reset_viz_pool()


@pytest.fixture
def ingest_module():
    from backend import ingest

    ingest._reset_for_tests()
    yield ingest
    ingest._reset_for_tests()


def _rows(dsn: str, sql: str):
    import psycopg

    with psycopg.connect(dsn) as connection:
        return connection.execute(db.sql_text(sql)).fetchall()


def _install_source(monkeypatch, ingest, snapshot):
    monkeypatch.setattr(ingest, "fetch_snapshot", lambda token, login: copy.deepcopy(snapshot))
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GH_USER", snapshot["account"]["login"])


def test_cold_sync_writes_one_current_row_per_node(ingest_db, ingest_module, monkeypatch):
    snapshot = _snapshot("snapshot_initial.json")
    _install_source(monkeypatch, ingest_module, snapshot)

    result = ingest_module.run_ingest("test")

    assert result["skipped"] is False
    assert result["data_changed"] is True
    assert result["repositories_fetched"] == 2
    assert result["issues_fetched"] == 2
    assert result["pull_requests_fetched"] == 2
    assert result["repositories_upserted"] == 2
    assert result["issues_upserted"] == 2
    assert result["pull_requests_upserted"] == 2
    assert _rows(ingest_db, "SELECT count(*) FROM repositories")[0][0] == 2
    assert _rows(ingest_db, "SELECT count(*) FROM issues")[0][0] == 2
    assert _rows(ingest_db, "SELECT count(*) FROM pull_requests")[0][0] == 2


def test_identical_snapshot_is_idempotent_and_does_not_notify(
    ingest_db, ingest_module, monkeypatch
):
    snapshot = _snapshot("snapshot_initial.json")
    _install_source(monkeypatch, ingest_module, snapshot)
    ingest_module.run_ingest("first")

    calls = []
    monkeypatch.setattr(ingest_module.cache.response_cache, "invalidate", lambda: calls.append("cache"))
    monkeypatch.setattr(ingest_module.events, "broadcast_threadsafe", lambda *args: calls.append("event"))
    result = ingest_module.run_ingest("second")

    assert result["data_changed"] is False
    assert result["repositories_upserted"] == 0
    assert result["issues_upserted"] == 0
    assert result["pull_requests_upserted"] == 0
    assert calls == []


def test_changed_issue_replaces_final_state(ingest_db, ingest_module, monkeypatch):
    initial = _snapshot("snapshot_initial.json")
    changed = _snapshot("snapshot_changed.json")
    _install_source(monkeypatch, ingest_module, initial)
    ingest_module.run_ingest("initial")
    _install_source(monkeypatch, ingest_module, changed)

    ingest_module.run_ingest("changed")

    row = _rows(
        ingest_db,
        "SELECT state, state_reason, closed_at FROM issues WHERE node_id = 'I_1'",
    )[0]
    assert row[0] == "CLOSED"
    assert row[1] == "NOT_PLANNED"
    assert row[2].astimezone(timezone.utc).isoformat().startswith(
        "2026-08-11T12:55:00"
    )


def test_complete_reconciliation_removes_unseen_rows(ingest_db, ingest_module, monkeypatch):
    initial = _snapshot("snapshot_initial.json")
    changed = _snapshot("snapshot_changed.json")
    _install_source(monkeypatch, ingest_module, initial)
    ingest_module.run_ingest("initial")
    _install_source(monkeypatch, ingest_module, changed)

    result = ingest_module.run_ingest("changed")

    assert result["repositories_deleted"] == 1
    assert result["issues_deleted"] == 1
    assert result["pull_requests_deleted"] == 1
    assert _rows(ingest_db, "SELECT count(*) FROM repositories")[0][0] == 1
    assert _rows(ingest_db, "SELECT count(*) FROM issues")[0][0] == 1
    assert _rows(ingest_db, "SELECT count(*) FROM pull_requests")[0][0] == 1


def test_external_owner_mutation_replaces_repository_and_item_context(
    ingest_db, ingest_module, monkeypatch
):
    initial = _snapshot("snapshot_initial.json")
    changed = copy.deepcopy(initial)
    changed["generated_at"] = "2026-08-11T13:00:00Z"
    changed["repositories"][0]["owner"]["login"] = "new-external"
    changed["repositories"][0]["nameWithOwner"] = "new-external/one"
    for collection in ("issues", "pull_requests"):
        for item in changed[collection]:
            if item["repository_id"] == "R_1":
                item["owner"] = "new-external"
                item["repository"] = "new-external/one"

    _install_source(monkeypatch, ingest_module, initial)
    ingest_module.run_ingest("initial")
    _install_source(monkeypatch, ingest_module, changed)

    result = ingest_module.run_ingest("owner-change")

    assert result["data_changed"] is True
    assert _rows(
        ingest_db,
        "SELECT owner_login, name_with_owner FROM repositories WHERE node_id = 'R_1'",
    )[0] == ("new-external", "new-external/one")
    assert _rows(
        ingest_db,
        "SELECT repository_id FROM issues WHERE node_id = 'I_1'",
    )[0][0] == "R_1"


@pytest.mark.parametrize("mutation", ["private", "account_owned"])
def test_invalid_visibility_or_owner_preserves_state_and_skips_hooks(
    ingest_db, ingest_module, monkeypatch, mutation
):
    from backend.ingest import SourceError

    initial = _snapshot("snapshot_initial.json")
    _install_source(monkeypatch, ingest_module, initial)
    ingest_module.run_ingest("initial")
    before_rows = _rows(
        ingest_db,
        "SELECT node_id, owner_login, name_with_owner FROM repositories ORDER BY node_id",
    )
    before_sync = _rows(
        ingest_db,
        "SELECT last_committed_at, last_source_snapshot_at FROM sync_state WHERE id = 1",
    )[0]

    invalid = copy.deepcopy(initial)
    invalid["generated_at"] = "2026-08-11T13:00:00Z"
    if mutation == "private":
        invalid["repositories"][0]["isPrivate"] = True
    else:
        invalid["repositories"][0]["owner"]["login"] = "octocat"
        invalid["repositories"][0]["nameWithOwner"] = "octocat/one"
    _install_source(monkeypatch, ingest_module, invalid)
    hooks = []
    monkeypatch.setattr(
        ingest_module.cache.response_cache,
        "invalidate",
        lambda: hooks.append("cache"),
    )
    monkeypatch.setattr(
        ingest_module.events,
        "broadcast_threadsafe",
        lambda *args: hooks.append(("event", args)),
    )

    with pytest.raises(SourceError):
        ingest_module.run_ingest(f"invalid-{mutation}")

    assert _rows(
        ingest_db,
        "SELECT node_id, owner_login, name_with_owner FROM repositories ORDER BY node_id",
    ) == before_rows
    assert _rows(
        ingest_db,
        "SELECT last_committed_at, last_source_snapshot_at FROM sync_state WHERE id = 1",
    )[0] == before_sync
    assert hooks == []
    error = _rows(
        ingest_db,
        f"SELECT error FROM ingest_runs WHERE trigger = 'invalid-{mutation}' ORDER BY id DESC LIMIT 1",
    )[0][0]
    assert error


def test_open_run_failure_resets_progress_and_does_not_finalize_missing_row(
    ingest_module, monkeypatch
):
    def fail_open(*args, **kwargs):
        raise RuntimeError("database unavailable while opening run")

    monkeypatch.setattr(ingest_module, "_open_run", fail_open)
    finalized = []
    monkeypatch.setattr(
        ingest_module,
        "_finish_failed_run",
        lambda *args: finalized.append(args),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        ingest_module.run_ingest("open-failure")

    progress = ingest_module.progress_snapshot()
    assert progress["phase"] == "idle"
    assert progress["done"] == 0
    assert progress["total"] == 0
    assert progress["run_id"] is None
    assert "database unavailable" in progress["last_error"]
    assert finalized == []


def test_failed_acquisition_preserves_current_rows_and_sync_state(
    ingest_db, ingest_module, monkeypatch
):
    from backend.ingest import SourceError

    snapshot = _snapshot("snapshot_initial.json")
    _install_source(monkeypatch, ingest_module, snapshot)
    ingest_module.run_ingest("initial")
    before = _rows(
        ingest_db,
        "SELECT last_committed_at, last_source_snapshot_at FROM sync_state WHERE id = 1",
    )[0]
    count_before = _rows(ingest_db, "SELECT count(*) FROM issues")[0][0]

    monkeypatch.setattr(
        ingest_module,
        "fetch_snapshot",
        lambda token, login: (_ for _ in ()).throw(SourceError("network down")),
    )
    with pytest.raises(SourceError):
        ingest_module.run_ingest("failed")

    assert _rows(ingest_db, "SELECT count(*) FROM issues")[0][0] == count_before
    after = _rows(
        ingest_db,
        "SELECT last_committed_at, last_source_snapshot_at FROM sync_state WHERE id = 1",
    )[0]
    assert after == before
    assert "network down" in _rows(
        ingest_db,
        "SELECT error FROM ingest_runs WHERE trigger = 'failed' ORDER BY id DESC LIMIT 1",
    )[0][0]


def test_mid_transaction_failure_rolls_back_all_current_state(
    ingest_db, ingest_module, monkeypatch
):
    initial = _snapshot("snapshot_initial.json")
    changed = _snapshot("snapshot_changed.json")
    _install_source(monkeypatch, ingest_module, initial)
    ingest_module.run_ingest("initial")
    before = _rows(ingest_db, "SELECT node_id, state_reason FROM issues ORDER BY node_id")

    _install_source(monkeypatch, ingest_module, changed)
    original = ingest_module._upsert_pull_requests
    hooks = []
    monkeypatch.setattr(
        ingest_module.cache.response_cache,
        "invalidate",
        lambda: hooks.append("cache"),
    )
    monkeypatch.setattr(
        ingest_module.events,
        "broadcast_threadsafe",
        lambda *args: hooks.append(("event", args)),
    )

    def fail_after_issue(*args, **kwargs):
        raise RuntimeError("simulated transaction failure")

    monkeypatch.setattr(ingest_module, "_upsert_pull_requests", fail_after_issue)
    with pytest.raises(RuntimeError, match="simulated transaction failure"):
        ingest_module.run_ingest("rollback")
    monkeypatch.setattr(ingest_module, "_upsert_pull_requests", original)

    assert _rows(ingest_db, "SELECT node_id, state_reason FROM issues ORDER BY node_id") == before
    assert _rows(ingest_db, "SELECT count(*) FROM repositories")[0][0] == 2
    assert hooks == []


def test_lock_contention_returns_truthful_skipped_summary(ingest_module):
    assert ingest_module._RUN_LOCK.acquire(blocking=False)
    try:
        result = ingest_module.run_ingest("concurrent")
    finally:
        ingest_module._RUN_LOCK.release()
    assert result == {
        "skipped": True,
        "reason": "ingest already running",
        "trigger": "concurrent",
    }


def test_post_commit_hooks_run_after_rows_are_visible(ingest_db, ingest_module, monkeypatch):
    snapshot = _snapshot("snapshot_initial.json")
    _install_source(monkeypatch, ingest_module, snapshot)
    observations = []

    def invalidate():
        observations.append(("cache", _rows(ingest_db, "SELECT count(*) FROM issues")[0][0]))

    def broadcast(event, payload):
        observations.append(
            (
                "event",
                event,
                payload["trigger"],
                payload["data_changed"],
                _rows(ingest_db, "SELECT count(*) FROM pull_requests")[0][0],
            )
        )

    monkeypatch.setattr(ingest_module.cache.response_cache, "invalidate", invalidate)
    monkeypatch.setattr(ingest_module.events, "broadcast_threadsafe", broadcast)

    ingest_module.run_ingest("post-commit")

    assert observations == [
        ("cache", 2),
        ("event", "ingest_done", "post-commit", True, 2),
    ]


def test_progress_snapshot_is_thread_safe_and_resets(ingest_module):
    first = ingest_module.progress_snapshot()
    assert first["phase"] == "idle"
    assert first["done"] == 0
    assert first["total"] == 0

    values = []
    threads = [threading.Thread(target=lambda: values.append(ingest_module.progress_snapshot())) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(values) == 20
    assert all(value["phase"] == "idle" for value in values)
