"""The guard that stops this suite running against a real database.

The suite TRUNCATEs, DROPs tables and runs `DELETE FROM users`. On the
deployment host a first run reached the production auth database — owned by
a different application on the same PostgreSQL instance — and issued that
DELETE. Nothing was lost, but only because the role happened to lack the
privilege. The guard in conftest.py exists so the next run cannot get that
far, and these tests exist so the guard cannot quietly stop working.

They test the guard's decision functions directly rather than the module
import, because by the time this file runs conftest has already imported
successfully — that IS the happy path, asserted at the bottom.
"""
import importlib.util
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _conftest():
    """Load conftest by path, as a module of its own.

    Importing it by name would collide with the already-imported conftest
    pytest owns; this gets a clean copy whose helpers can be poked at.
    """
    spec = importlib.util.spec_from_file_location(
        "_conftest_under_test", REPO_ROOT / "tests" / "conftest.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONFTEST = _conftest()


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        ("postgresql:///ghpulse_test", "ghpulse_test"),
        ("postgresql:///auth_test", "auth_test"),
        ("postgresql://postgres:pw@127.0.0.1:5432/ghpulse_test", "ghpulse_test"),
        ("postgresql://host/ghpulse_test?sslmode=require", "ghpulse_test"),
        ("postgresql://host/prod_db?options=-csearch_path%3Dfoo", "prod_db"),
        ("postgresql://host", ""),
    ],
)
def test_database_name_extraction(dsn, expected):
    assert CONFTEST._database_name(dsn) == expected


def test_database_name_never_returns_credentials():
    """The name is what lands in the error message, so it must be only that.

    A DSN carries a password; an error message ends up in tracebacks, CI
    logs and bug reports. This is the property that keeps the two apart.
    """
    dsn = "postgresql://someuser:hunter2@db.internal:5432/ghpulse_test"

    name = CONFTEST._database_name(dsn)

    assert name == "ghpulse_test"
    assert "hunter2" not in name
    assert "someuser" not in name


@pytest.mark.parametrize(
    "name",
    ["test", "test_ghpulse", "ghpulse_test", "auth_test", "a_test_b", "GHPULSE_TEST"],
)
def test_names_accepted_as_test_databases(name):
    assert CONFTEST._is_test_database(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "",
        "ghpulse",
        "postgres",
        # The reason this is not a substring check: each of these contains
        # "test" and none of them is a test database.
        "attestation",
        "latest",
        "contest",
        "testudo",
    ],
)
def test_names_rejected_as_non_test_databases(name):
    assert CONFTEST._is_test_database(name) is False


def test_guard_raises_on_a_production_dsn(monkeypatch):
    """The whole point: a real DSN in the environment aborts the run."""
    monkeypatch.setenv("DATABASE_URL_VIZ", "postgresql:///ghpulse")
    monkeypatch.setenv("DATABASE_URL_AUTH", "postgresql:///ghpulse_test")

    with pytest.raises(RuntimeError, match="refusing to run"):
        CONFTEST._assert_test_databases()


def test_guard_checks_the_auth_dsn_too(monkeypatch):
    """The auth database is the one that nearly lost rows, and it belongs to
    a different application — so it matters more, not less."""
    monkeypatch.setenv("DATABASE_URL_VIZ", "postgresql:///ghpulse_test")
    monkeypatch.setenv("DATABASE_URL_AUTH", "postgresql://user:pw@host/real_auth_db")

    with pytest.raises(RuntimeError) as excinfo:
        CONFTEST._assert_test_databases()

    message = str(excinfo.value)
    assert "DATABASE_URL_AUTH" in message
    assert "real_auth_db" in message
    # The DSN carried a password. It must not have travelled into the error.
    assert "pw@" not in message
    assert "user:" not in message


def test_guard_checks_the_integration_dsn(monkeypatch):
    """GHPULSE_TEST_DATABASE_URL is read straight by the file that runs
    `DELETE FROM users`, so leaving it unchecked would leave the hole open."""
    monkeypatch.setenv("DATABASE_URL_VIZ", "postgresql:///ghpulse_test")
    monkeypatch.setenv("DATABASE_URL_AUTH", "postgresql:///auth_test")
    monkeypatch.setenv("GHPULSE_TEST_DATABASE_URL", "postgresql:///production")

    with pytest.raises(RuntimeError, match="GHPULSE_TEST_DATABASE_URL"):
        CONFTEST._assert_test_databases()


def test_guard_reports_every_offender_at_once(monkeypatch):
    """Fixing one and re-running to discover the next wastes a cycle each."""
    monkeypatch.setenv("DATABASE_URL_VIZ", "postgresql:///prod_viz")
    monkeypatch.setenv("DATABASE_URL_AUTH", "postgresql:///prod_auth")

    with pytest.raises(RuntimeError) as excinfo:
        CONFTEST._assert_test_databases()

    assert "prod_viz" in str(excinfo.value)
    assert "prod_auth" in str(excinfo.value)


def test_guard_ignores_variables_that_are_not_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL_VIZ", "postgresql:///ghpulse_test")
    monkeypatch.setenv("DATABASE_URL_AUTH", "postgresql:///auth_test")
    monkeypatch.delenv("GHPULSE_TEST_DATABASE_URL", raising=False)

    CONFTEST._assert_test_databases()


def test_the_current_run_is_itself_against_test_databases():
    """The happy path, asserted against the real environment.

    If this fails, the suite you are running right now is pointed somewhere
    it should not be — and conftest should already have aborted before
    reaching here.
    """
    for var in CONFTEST._TEST_DSN_VARS:
        dsn = os.environ.get(var)
        if not dsn:
            continue
        assert CONFTEST._is_test_database(CONFTEST._database_name(dsn)), (
            f"{var} does not point at a test database"
        )
