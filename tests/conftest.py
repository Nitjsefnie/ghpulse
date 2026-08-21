import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Set DSNs before importing any application module. `setdefault` only fills
# in a value when the environment does not already carry one — so on a host
# where .env has been loaded, the PRODUCTION DSNs win and every
# fixture below points at live databases.
os.environ.setdefault("DATABASE_URL_VIZ", "postgresql:///ghpulse_test")
os.environ.setdefault("DATABASE_URL_AUTH", "postgresql:///auth_test")
os.environ.setdefault("ADMIN_TOKEN", "test-admin")
os.environ.setdefault("COOKIE_SECURE", "0")


# --- refuse to run against anything that is not a test database ------------
#
# This suite is destructive by design: it TRUNCATEs, DROPs tables, and runs
# `DELETE FROM users` in teardown. That is fine against a throwaway database
# and catastrophic against a real one, and `setdefault` above chooses the
# real one precisely when real configuration is in scope.
#
# It has already happened. On the deployment host a first run reached the
# production auth database — owned by a DIFFERENT application on the same
# PostgreSQL instance — and issued `DELETE FROM users` against it. Nothing
# was lost, but only because that role happened to hold SELECT and UPDATE
# and no DELETE. A role with the privileges an application normally has
# would have emptied someone else's auth table.
#
# So the naming convention is enforced rather than assumed, and the failure
# is loud and immediate: the run aborts during collection, before any
# fixture opens a connection.

_TEST_DSN_VARS = (
    "DATABASE_URL_VIZ",
    "DATABASE_URL_AUTH",
    # Read directly by tests/test_final_integration.py, which is also the
    # file that runs `DELETE FROM users`.
    "GHPULSE_TEST_DATABASE_URL",
)


def _database_name(dsn: str) -> str:
    """The database a DSN points at, or "" if it names none.

    Deliberately hand-rolled rather than urlparse'd: a DSN can carry
    credentials, and everything this module does with the result ends up in
    an error message. Taking only the path segment means a password cannot
    reach a traceback, a CI log, or a bug report.
    """
    without_query = dsn.split("?", 1)[0]
    # Strip the scheme, then take the path AFTER the authority. Splitting on
    # the last "/" instead would read `postgresql://ghpulse_test` — a HOST
    # named that, with no database path — as the database `ghpulse_test` and
    # wave it through. The guard's own tests cover that case.
    _, _, after_scheme = without_query.partition("://")
    if not after_scheme:
        after_scheme = without_query
    authority, sep, path = after_scheme.partition("/")
    del authority
    if not sep:
        return ""
    return path.strip()


def _is_test_database(name: str) -> bool:
    """Whether a database name is unambiguously a throwaway.

    A substring check would accept `attestation` or `latest_metrics`; these
    are the shapes an intentional test database actually takes.
    """
    if not name:
        return False
    lowered = name.lower()
    return (
        lowered == "test"
        or lowered.startswith("test_")
        or lowered.endswith("_test")
        or "_test_" in lowered
    )


def _assert_test_databases() -> None:
    offenders = []
    for var in _TEST_DSN_VARS:
        dsn = os.environ.get(var)
        if not dsn:
            continue
        name = _database_name(dsn)
        if not _is_test_database(name):
            # The NAME only — never the DSN, which may carry a password.
            offenders.append(f"{var} -> database {name or '(unnamed)'!r}")

    if offenders:
        raise RuntimeError(
            "refusing to run the test suite against a non-test database.\n\n"
            + "\n".join(f"  {line}" for line in offenders)
            + "\n\nThis suite TRUNCATEs tables, DROPs them, and runs "
            "`DELETE FROM users`.\nIt must only ever point at a throwaway "
            "database whose name is\n`test`, or starts with `test_`, or ends "
            "with `_test`.\n\nThis usually means .env is loaded in "
            "this shell and its\nproduction DSNs are winning the "
            "`setdefault` above. Unset them, or\nset the variables above "
            "explicitly to test databases, and re-run."
        )


_assert_test_databases()


# environment setup and this import, so the import is genuinely not at
# the top of the file — and it cannot move, because backend modules read
# these variables at import time.
from backend import cache  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_response_cache():
    # response_cache is a process-global. Two tests with different fixtures
    # but identical query params would otherwise read each other's payloads.
    cache.response_cache.clear()
    yield
    cache.response_cache.clear()
