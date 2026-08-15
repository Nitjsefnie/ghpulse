"""Integration contracts for the PostgreSQL connection pools."""
from __future__ import annotations

import os

import psycopg
import pytest

from backend import db


@pytest.mark.parametrize(
    ("pool_factory", "reset_pool", "dsn_name"),
    [
        pytest.param(db.viz_pool, db.reset_viz_pool, "DATABASE_URL_VIZ", id="viz"),
        pytest.param(db.auth_pool, db.reset_auth_pool, "DATABASE_URL_AUTH", id="auth"),
    ],
)
def test_pool_replaces_connection_terminated_while_idle(
    pool_factory, reset_pool, dsn_name
):
    """A dead server-side connection must not be handed out on checkout."""
    dsn = os.environ[dsn_name]
    reset_pool()
    pool = pool_factory()
    pool.open(wait=True)

    try:
        # Keep every other initially available connection checked out so the
        # terminated connection is the only one eligible for the next request.
        with pool.connection():
            with pool.connection() as target:
                pid_row = target.execute("SELECT pg_backend_pid()").fetchone()
                assert pid_row is not None
                target_pid = pid_row[0]

            with psycopg.connect(dsn, autocommit=True) as killer:
                termination_row = killer.execute(
                    "SELECT pg_terminate_backend(%s)", (target_pid,)
                ).fetchone()
            assert termination_row is not None
            terminated = termination_row[0]
            assert terminated is True

            with pool.connection() as replacement:
                assert replacement.execute("SELECT 1").fetchone() == (1,)
    finally:
        reset_pool()
