"""Independent PostgreSQL pools for dashboard data and authentication.

The visualization database owns GitHub snapshots. The authentication database
is an external users database and is accessed only for the user's JSON config.
The pools intentionally have separate lifecycles and are never joined.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import cast

from psycopg.abc import Query
from psycopg_pool import ConnectionPool

# These globals are the deliberate process-local pool registry. The broad
# close guard is limited to cleanup: a failed close must not mask the original
# startup/test failure.
# pylint: disable=global-statement,broad-exception-caught

_VIZ: ConnectionPool | None = None
_AUTH: ConnectionPool | None = None


def sql_text(query: str) -> Query:
    """Mark a programmatically assembled query string as executable.

    psycopg 3.2 types Cursor.execute() as taking a LiteralString, so a
    static checker flags every runtime-built SQL string. The queries
    passed through here are assembled exclusively from our own fragments
    — branch-selected WHERE clauses, integer bucket widths — and user
    input only ever reaches the DB as a %s parameter, so the
    literal-only guarantee the type wants is upheld by construction.
    """
    return cast(Query, query)


def viz_pool() -> ConnectionPool:
    """Return the lazily constructed visualization pool."""
    global _VIZ
    if _VIZ is None:
        _VIZ = ConnectionPool(
            os.environ.get("DATABASE_URL_VIZ", "postgresql:///ghpulse"),
            # The read endpoints are sync (blocking psycopg) and run on
            # FastAPI's threadpool, so requests now hit the DB genuinely
            # concurrently — a single dashboard load fans out to ~7. While
            # they were async-on-the-event-loop they serialised and 8 was
            # never exercised; it would now be the bottleneck.
            min_size=2, max_size=20, timeout=10,
            kwargs={"autocommit": False},
            check=ConnectionPool.check_connection,
            # Pool ownership belongs to the FastAPI lifespan.  psycopg-pool's
            # implicit eager-open path is deprecated and also binds async
            # pool state to whichever loop happened to import this module.
            open=False,
        )
    return _VIZ


def reset_viz_pool() -> None:
    """Close and drop the cached viz pool so the next viz_pool() call
    re-reads DATABASE_URL_VIZ. Test suites need this between scratch-DB
    configurations; production code never calls it."""
    global _VIZ
    if _VIZ is not None:
        try:
            _VIZ.close()
        except Exception:
            pass
    _VIZ = None


def auth_pool() -> ConnectionPool:
    """Return the lazily constructed authentication pool."""
    global _AUTH
    if _AUTH is None:
        _AUTH = ConnectionPool(
            os.environ.get("DATABASE_URL_AUTH", "postgresql:///auth"),
            min_size=1, max_size=4, timeout=10,
            kwargs={"autocommit": True},
            check=ConnectionPool.check_connection,
            open=False,
        )
    return _AUTH


def reset_auth_pool() -> None:
    """Close the cached auth pool and make the next call recreate it."""
    global _AUTH
    if _AUTH is not None:
        try:
            _AUTH.close()
        except Exception:
            pass
    _AUTH = None


def close_pools() -> None:
    """Close both pools independently during application shutdown/tests."""
    reset_viz_pool()
    reset_auth_pool()


def open_pools(*, wait: bool = True) -> tuple[ConnectionPool, ConnectionPool]:
    """Open and return the independent pools owned by the application.

    Pool construction is deliberately separate from opening.  This keeps
    import-time/test setup side-effect free and makes the lifespan the one
    place that binds pool workers to the serving process.
    """
    viz = viz_pool()
    auth = auth_pool()
    if viz.closed:
        viz.open(wait=wait)
    if auth.closed:
        auth.open(wait=wait)
    return viz, auth


def _ensure_open(pool: ConnectionPool) -> None:
    """Support direct module consumers while lifespan remains authoritative."""
    if pool.closed:
        pool.open(wait=True)


@contextmanager
def viz_conn():
    """Yield one connection from the visualization pool."""
    pool = viz_pool()
    _ensure_open(pool)
    with pool.connection() as conn:
        yield conn


@contextmanager
def auth_conn():
    """Yield one connection from the authentication pool."""
    pool = auth_pool()
    _ensure_open(pool)
    with pool.connection() as conn:
        yield conn


def schema_check() -> None:
    """Fail fast at startup if either DB's required shape is missing.

    For ghpulse: the current-state repository table exists.
    For the auth DB: 'users' table has a JSONB 'config' column.
    Raises RuntimeError on any mismatch.
    """
    with viz_conn() as c:
        row = c.execute(
            "SELECT to_regclass('public.repositories')"
        ).fetchone()
        if row is None or row[0] is None:
            raise RuntimeError(
                "ghpulse.repositories missing — run backend/schema.sql"
            )
    with auth_conn() as c:
        row = c.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='users' "
            "AND column_name='config'"
        ).fetchone()
        if row is None:
            raise RuntimeError(
                "auth DB users table has no 'config' column"
            )
        if row[0] != "jsonb":
            raise RuntimeError(
                f"auth DB users.config must be JSONB, got {row[0]!r}"
            )


def load_dotenv(path: str = ".env") -> None:
    """Tiny dotenv loader. Avoids the python-dotenv dependency.

    Constraints (intentional simplicity — keep values plain):
    - Values are read literally; quotes are NOT stripped. ADMIN_TOKEN="abc"
      stores the value with the literal quotes.
    - The 'export ' prefix is NOT supported (the line key would become
      'export ADMIN_TOKEN', not 'ADMIN_TOKEN').
    - The first '=' splits key from value, so values may contain '=' freely.
    - Existing env vars are NEVER overwritten (uses os.environ.setdefault).
    - Comment lines (starting with '#') and blank lines are skipped.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
