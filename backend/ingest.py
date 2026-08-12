"""Complete current-state GitHub snapshot ingestion.

The source boundary returns one validated, public snapshot.  Ingest holds a
process-local non-blocking lock while it acquires that snapshot and applies
the complete replacement in one PostgreSQL transaction.  Current tables are
the authority: an identical snapshot is a no-op, while an unseen node is
removed only after every source record has been validated and upserted.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import logging
import os
import threading
from typing import Any, Iterator

from backend import cache, db, events, github_source
from backend.api_common import SYNC_FAILURE_CODE, SYNC_FAILURE_MESSAGE

# The validator intentionally uses exact type checks and a single explicit
# boundary function; its branches/locals are the security contract, not a
# generic application routine. Cleanup and post-commit hooks also deliberately
# catch broad failures so the committed snapshot remains authoritative.
# pylint: disable=unidiomatic-typecheck,too-many-locals,too-many-branches
# pylint: disable=too-many-statements,too-many-arguments,too-many-positional-arguments
# pylint: disable=broad-exception-caught

log = logging.getLogger("ghpulse.ingest")
MAX_STATE_REASON_LENGTH = 64


class SourceError(RuntimeError):
    """Raised when acquisition or snapshot validation fails."""


class IngestError(RuntimeError):
    """Raised when a complete snapshot cannot be committed."""


_RUN_LOCK = threading.Lock()
_PROGRESS_LOCK = threading.Lock()
_PROGRESS: dict[str, Any] = {
    "phase": "idle",
    "done": 0,
    "total": 0,
    "run_id": None,
    "started_at": None,
    "last_error": None,
}


def progress_snapshot() -> dict:
    """Return a consistent copy of the current in-process run progress."""
    with _PROGRESS_LOCK:
        return dict(_PROGRESS)


def _set_progress(**updates: Any) -> None:
    with _PROGRESS_LOCK:
        _PROGRESS.update(updates)


def _reset_progress(error: str | None = None) -> None:
    _set_progress(
        phase="idle",
        done=0,
        total=0,
        run_id=None,
        started_at=None,
        last_error=error,
    )


def _reset_for_tests() -> None:
    """Reset process-local state for isolated test cases."""
    _reset_progress()


@contextmanager
def _run_lock_nonblocking() -> Iterator[bool]:
    acquired = _RUN_LOCK.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _RUN_LOCK.release()


def fetch_snapshot(token: str, login: str) -> dict:
    """An explicit seam around the pinned source adapter.

    Keeping this wrapper means tests can replace acquisition without touching
    the vendor module, while production always follows the checked-out pin.
    """
    return github_source.fetch_snapshot(token, login)


def _configured_credentials() -> tuple[str, str]:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    login = os.environ.get("GH_USER") or os.environ.get("GITHUB_USER")
    if not token or not login:
        missing = []
        if not token:
            missing.append("GH_TOKEN")
        if not login:
            missing.append("GH_USER")
        raise SourceError("missing GitHub ingest configuration: " + ", ".join(missing))
    return token, login


def _parse_timestamp(value: Any, context: str, *, nullable: bool = False) -> datetime | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SourceError(f"{context} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceError(f"{context} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceError(f"{context} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _require_dict(value: Any, context: str) -> dict:
    if type(value) is not dict:
        raise SourceError(f"{context} must be an object")
    return value


def _require_string(value: Any, context: str) -> str:
    if type(value) is not str or not value:
        raise SourceError(f"{context} must be a non-empty string")
    return value


def _require_bool(value: Any, context: str) -> bool:
    if type(value) is not bool:
        raise SourceError(f"{context} must be a boolean")
    return value


def _validate_snapshot(snapshot: Any) -> tuple[datetime, str, list[dict], list[dict], list[dict]]:
    """Validate the source boundary again immediately before persistence."""
    body = _require_dict(snapshot, "snapshot")
    expected_top = {
        "schema_version",
        "generated_at",
        "account",
        "repositories",
        "issues",
        "pull_requests",
    }
    if set(body) != expected_top:
        missing = sorted(expected_top - set(body))
        unknown = sorted(set(body) - expected_top)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise SourceError("invalid snapshot fields: " + "; ".join(details))
    if type(body["schema_version"]) is not int or body["schema_version"] != 1:
        raise SourceError("unsupported snapshot schema_version")

    source_at = _parse_timestamp(body["generated_at"], "snapshot.generated_at")
    assert source_at is not None
    account = _require_dict(body["account"], "snapshot.account")
    if set(account) != {"login"}:
        raise SourceError("snapshot.account must contain only login")
    login = _require_string(account["login"], "snapshot.account.login")
    for collection in ("repositories", "issues", "pull_requests"):
        if type(body[collection]) is not list:
            raise SourceError(f"snapshot.{collection} must be a list")

    repositories: list[dict] = []
    repositories_by_id: dict[str, dict] = {}
    seen_ids: set[str] = set()
    account_key = login.casefold()
    for index, raw in enumerate(body["repositories"]):
        repo = _require_dict(raw, f"snapshot.repositories[{index}]")
        if set(repo) != {"id", "nameWithOwner", "url", "isPrivate", "owner"}:
            raise SourceError(f"snapshot.repositories[{index}] has invalid fields")
        node_id = _require_string(repo["id"], f"snapshot.repositories[{index}].id")
        if node_id in seen_ids:
            raise SourceError(f"duplicate snapshot node_id: {node_id}")
        seen_ids.add(node_id)
        if _require_bool(repo["isPrivate"], f"snapshot.repositories[{index}].isPrivate"):
            raise SourceError("private repositories are not allowed in ingest")
        owner = _require_dict(repo["owner"], f"snapshot.repositories[{index}].owner")
        if set(owner) != {"login"}:
            raise SourceError(f"snapshot.repositories[{index}].owner has invalid fields")
        owner_login = _require_string(owner["login"], "repository owner login")
        if owner_login.casefold() == account_key:
            raise SourceError("account-owned repositories are not external")
        name = _require_string(repo["nameWithOwner"], "repository nameWithOwner")
        url = _require_string(repo["url"], "repository url")
        normal = {
            "node_id": node_id,
            "name_with_owner": name,
            "owner_login": owner_login,
            "url": url,
        }
        repositories.append(normal)
        repositories_by_id[node_id] = normal

    def validate_item(raw: Any, index: int, kind: str) -> dict:
        item = _require_dict(raw, f"snapshot.{kind}[{index}]")
        fields = {
            "node_id",
            "repository_id",
            "repository",
            "owner",
            "repository_url",
            "is_private",
            "number",
            "url",
            "created_at",
            "updated_at",
            "closed_at",
            "state",
            "state_reason",
        }
        if kind == "pull_requests":
            fields.remove("state_reason")
            fields.update({"merged_at", "merged"})
        if set(item) != fields:
            raise SourceError(f"snapshot.{kind}[{index}] has invalid fields")
        node_id = _require_string(item["node_id"], f"{kind} node_id")
        if node_id in seen_ids:
            raise SourceError(f"duplicate snapshot node_id: {node_id}")
        seen_ids.add(node_id)
        repository_id = _require_string(item["repository_id"], f"{kind} repository_id")
        repo = repositories_by_id.get(repository_id)
        if repo is None:
            raise SourceError(f"{kind} references an absent repository")
        if _require_bool(item["is_private"], f"{kind} is_private"):
            raise SourceError("private items are not allowed in ingest")
        owner = _require_string(item["owner"], f"{kind} owner")
        if owner.casefold() == account_key:
            raise SourceError("account-owned items are not external")
        if owner != repo["owner_login"] or item["repository"] != repo["name_with_owner"]:
            raise SourceError(f"{kind} repository ownership does not match its repository")
        if item["repository_url"] != repo["url"]:
            raise SourceError(f"{kind} repository URL does not match its repository")
        number = item["number"]
        if type(number) is not int or number < 1:
            raise SourceError(f"{kind} number must be positive")
        for field in ("repository", "repository_url", "url", "state"):
            _require_string(item[field], f"{kind} {field}")
        created_at = _parse_timestamp(item["created_at"], f"{kind} created_at")
        updated_at = _parse_timestamp(item["updated_at"], f"{kind} updated_at")
        closed_at = _parse_timestamp(item["closed_at"], f"{kind} closed_at", nullable=True)
        state = item["state"]
        if kind == "issues":
            reason = item["state_reason"]
            if reason is not None:
                _require_string(reason, f"{kind} state_reason")
                if len(reason) > MAX_STATE_REASON_LENGTH or not reason.strip():
                    raise SourceError("invalid issue state_reason")
            if state not in {"OPEN", "CLOSED"}:
                raise SourceError("invalid issue state")
            if state == "OPEN" and (
                closed_at is not None or reason in {"COMPLETED", "NOT_PLANNED"}
            ):
                raise SourceError("open issue has inconsistent final-state fields")
            if state == "CLOSED" and (
                closed_at is None or reason in {None, "REOPENED"}
            ):
                raise SourceError("closed issue has inconsistent final-state fields")
            return {
                "node_id": node_id,
                "repository_id": repository_id,
                "number": number,
                "url": item["url"],
                "created_at": created_at,
                "updated_at": updated_at,
                "closed_at": closed_at,
                "state": state,
                "state_reason": reason,
            }

        merged = _require_bool(item["merged"], f"{kind} merged")
        merged_at = _parse_timestamp(item["merged_at"], f"{kind} merged_at", nullable=True)
        if state not in {"OPEN", "CLOSED", "MERGED"}:
            raise SourceError("invalid pull request state")
        if state == "OPEN" and (merged or closed_at is not None or merged_at is not None):
            raise SourceError("open pull request has inconsistent final-state fields")
        if state == "CLOSED" and (merged or closed_at is None or merged_at is not None):
            raise SourceError("closed pull request has inconsistent final-state fields")
        if state == "MERGED" and (not merged or closed_at is None or merged_at is None):
            raise SourceError("merged pull request has inconsistent final-state fields")
        return {
            "node_id": node_id,
            "repository_id": repository_id,
            "number": number,
            "url": item["url"],
            "created_at": created_at,
            "updated_at": updated_at,
            "closed_at": closed_at,
            "merged_at": merged_at,
            "state": state,
            "merged": merged,
        }

    issues = [validate_item(value, index, "issues") for index, value in enumerate(body["issues"])]
    pull_requests = [
        validate_item(value, index, "pull_requests")
        for index, value in enumerate(body["pull_requests"])
    ]
    return source_at, login, repositories, issues, pull_requests


def _open_run(started_at: datetime, trigger: str) -> int:
    with db.viz_conn() as connection:
        row = connection.execute(
            "INSERT INTO ingest_runs (trigger, started_at) VALUES (%s, %s) RETURNING id",
            (trigger, started_at),
        ).fetchone()
        connection.commit()
        if row is None:  # pragma: no cover - PostgreSQL guarantees RETURNING
            raise IngestError("could not create ingest run record")
        return int(row[0])


def _finish_failed_run(run_id: int, finished_at: datetime, error: str) -> None:
    try:
        with db.viz_conn() as connection:
            connection.execute(
                "UPDATE ingest_runs SET finished_at = %s, error = %s WHERE id = %s",
                (finished_at, error, run_id),
            )
            connection.execute(
                """
                INSERT INTO sync_state
                    (id, last_attempt_at, last_attempt_status,
                     last_attempt_error, updated_at)
                VALUES (1, %s, 'failure', %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    last_attempt_at = EXCLUDED.last_attempt_at,
                    last_attempt_status = EXCLUDED.last_attempt_status,
                    last_attempt_error = EXCLUDED.last_attempt_error,
                    updated_at = EXCLUDED.updated_at
                """,
                (finished_at, error, finished_at),
            )
            connection.commit()
    except Exception:  # pragma: no cover - only reached when the DB is unhealthy
        log.exception("could not record failed ingest run %s", run_id)


def _record_failed_attempt(finished_at: datetime, error: str) -> None:
    """Persist failure health even when the audit row could not be opened."""
    try:
        with db.viz_conn() as connection:
            connection.execute(
                """
                INSERT INTO sync_state
                    (id, last_attempt_at, last_attempt_status,
                     last_attempt_error, updated_at)
                VALUES (1, %s, 'failure', %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    last_attempt_at = EXCLUDED.last_attempt_at,
                    last_attempt_status = EXCLUDED.last_attempt_status,
                    last_attempt_error = EXCLUDED.last_attempt_error,
                    updated_at = EXCLUDED.updated_at
                """,
                (finished_at, error, finished_at),
            )
            connection.commit()
    except Exception:  # pragma: no cover - only reached when the DB is unhealthy
        log.exception("could not record failed ingest attempt")


def _upsert_repositories(connection, repositories: list[dict], observed_at: datetime) -> int:
    changed = 0
    query = """
        INSERT INTO repositories
            (node_id, name_with_owner, owner_login, url, is_private,
             is_external, first_seen_at, last_seen_at)
        VALUES (%s, %s, %s, %s, FALSE, TRUE, %s, %s)
        ON CONFLICT (node_id) DO UPDATE SET
            name_with_owner = EXCLUDED.name_with_owner,
            owner_login = EXCLUDED.owner_login,
            url = EXCLUDED.url,
            is_private = EXCLUDED.is_private,
            is_external = EXCLUDED.is_external,
            last_seen_at = EXCLUDED.last_seen_at
        WHERE repositories.name_with_owner IS DISTINCT FROM EXCLUDED.name_with_owner
           OR repositories.owner_login IS DISTINCT FROM EXCLUDED.owner_login
           OR repositories.url IS DISTINCT FROM EXCLUDED.url
           OR repositories.is_private IS DISTINCT FROM EXCLUDED.is_private
           OR repositories.is_external IS DISTINCT FROM EXCLUDED.is_external
        RETURNING node_id
    """
    for repository in repositories:
        row = connection.execute(
            query,
            (
                repository["node_id"],
                repository["name_with_owner"],
                repository["owner_login"],
                repository["url"],
                observed_at,
                observed_at,
            ),
        ).fetchone()
        changed += row is not None
    return changed


def _upsert_issues(connection, issues: list[dict]) -> int:
    changed = 0
    query = """
        INSERT INTO issues
            (node_id, repository_id, number, url, is_private, created_at,
             updated_at, closed_at, state, state_reason)
        VALUES (%s, %s, %s, %s, FALSE, %s, %s, %s, %s, %s)
        ON CONFLICT (node_id) DO UPDATE SET
            repository_id = EXCLUDED.repository_id,
            number = EXCLUDED.number,
            url = EXCLUDED.url,
            is_private = EXCLUDED.is_private,
            created_at = EXCLUDED.created_at,
            updated_at = EXCLUDED.updated_at,
            closed_at = EXCLUDED.closed_at,
            state = EXCLUDED.state,
            state_reason = EXCLUDED.state_reason
        WHERE issues.repository_id IS DISTINCT FROM EXCLUDED.repository_id
           OR issues.number IS DISTINCT FROM EXCLUDED.number
           OR issues.url IS DISTINCT FROM EXCLUDED.url
           OR issues.is_private IS DISTINCT FROM EXCLUDED.is_private
           OR issues.created_at IS DISTINCT FROM EXCLUDED.created_at
           OR issues.updated_at IS DISTINCT FROM EXCLUDED.updated_at
           OR issues.closed_at IS DISTINCT FROM EXCLUDED.closed_at
           OR issues.state IS DISTINCT FROM EXCLUDED.state
           OR issues.state_reason IS DISTINCT FROM EXCLUDED.state_reason
        RETURNING node_id
    """
    for issue in issues:
        row = connection.execute(
            query,
            (
                issue["node_id"],
                issue["repository_id"],
                issue["number"],
                issue["url"],
                issue["created_at"],
                issue["updated_at"],
                issue["closed_at"],
                issue["state"],
                issue["state_reason"],
            ),
        ).fetchone()
        changed += row is not None
    return changed


def _upsert_pull_requests(connection, pull_requests: list[dict]) -> int:
    changed = 0
    query = """
        INSERT INTO pull_requests
            (node_id, repository_id, number, url, is_private, created_at,
             updated_at, closed_at, merged_at, state, merged)
        VALUES (%s, %s, %s, %s, FALSE, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (node_id) DO UPDATE SET
            repository_id = EXCLUDED.repository_id,
            number = EXCLUDED.number,
            url = EXCLUDED.url,
            is_private = EXCLUDED.is_private,
            created_at = EXCLUDED.created_at,
            updated_at = EXCLUDED.updated_at,
            closed_at = EXCLUDED.closed_at,
            merged_at = EXCLUDED.merged_at,
            state = EXCLUDED.state,
            merged = EXCLUDED.merged
        WHERE pull_requests.repository_id IS DISTINCT FROM EXCLUDED.repository_id
           OR pull_requests.number IS DISTINCT FROM EXCLUDED.number
           OR pull_requests.url IS DISTINCT FROM EXCLUDED.url
           OR pull_requests.is_private IS DISTINCT FROM EXCLUDED.is_private
           OR pull_requests.created_at IS DISTINCT FROM EXCLUDED.created_at
           OR pull_requests.updated_at IS DISTINCT FROM EXCLUDED.updated_at
           OR pull_requests.closed_at IS DISTINCT FROM EXCLUDED.closed_at
           OR pull_requests.merged_at IS DISTINCT FROM EXCLUDED.merged_at
           OR pull_requests.state IS DISTINCT FROM EXCLUDED.state
           OR pull_requests.merged IS DISTINCT FROM EXCLUDED.merged
        RETURNING node_id
    """
    for pull_request in pull_requests:
        row = connection.execute(
            query,
            (
                pull_request["node_id"],
                pull_request["repository_id"],
                pull_request["number"],
                pull_request["url"],
                pull_request["created_at"],
                pull_request["updated_at"],
                pull_request["closed_at"],
                pull_request["merged_at"],
                pull_request["state"],
                pull_request["merged"],
            ),
        ).fetchone()
        changed += row is not None
    return changed


def _delete_unseen(connection, table: str, seen_ids: list[str]) -> int:
    # ``table`` comes only from the three literal call sites below; no user
    # input is interpolated into SQL.
    query = f"DELETE FROM {table} WHERE NOT (node_id = ANY(%s)) RETURNING node_id"
    return len(connection.execute(query, (seen_ids,)).fetchall())


def _commit_snapshot(
    run_id: int,
    started_at: datetime,
    source_at: datetime,
    repositories: list[dict],
    issues: list[dict],
    pull_requests: list[dict],
) -> dict:
    committed_at = datetime.now(timezone.utc)
    total = len(repositories) + len(issues) + len(pull_requests)
    _set_progress(phase="committing", total=total, done=0)
    with db.viz_conn() as connection:
        try:
            repositories_upserted = _upsert_repositories(
                connection, repositories, committed_at
            )
            _set_progress(done=len(repositories))
            issues_upserted = _upsert_issues(connection, issues)
            _set_progress(done=len(repositories) + len(issues))
            pull_requests_upserted = _upsert_pull_requests(connection, pull_requests)
            _set_progress(done=total)

            # Observation metadata is not dashboard state and therefore does
            # not turn an identical snapshot into a data-changing run.  It is
            # nevertheless truthful to refresh last_seen_at for every
            # repository present in this successfully acquired snapshot.
            connection.execute(
                "UPDATE repositories SET last_seen_at = %s WHERE node_id = ANY(%s)",
                (committed_at, [repository["node_id"] for repository in repositories]),
            )

            issues_deleted = _delete_unseen(
                connection, "issues", [item["node_id"] for item in issues]
            )
            pull_requests_deleted = _delete_unseen(
                connection,
                "pull_requests",
                [item["node_id"] for item in pull_requests],
            )
            repositories_deleted = _delete_unseen(
                connection,
                "repositories",
                [repository["node_id"] for repository in repositories],
            )
            data_changed = any(
                (
                    repositories_upserted,
                    issues_upserted,
                    pull_requests_upserted,
                    repositories_deleted,
                    issues_deleted,
                    pull_requests_deleted,
                )
            )
            connection.execute(
                """
                INSERT INTO sync_state (id, last_committed_at,
                                        last_source_snapshot_at,
                                        last_attempt_at,
                                        last_attempt_status,
                                        last_attempt_error,
                                        updated_at)
                VALUES (1, %s, %s, %s, 'success', NULL, %s)
                ON CONFLICT (id) DO UPDATE SET
                    last_committed_at = EXCLUDED.last_committed_at,
                    last_source_snapshot_at = EXCLUDED.last_source_snapshot_at,
                    last_attempt_at = EXCLUDED.last_attempt_at,
                    last_attempt_status = EXCLUDED.last_attempt_status,
                    last_attempt_error = EXCLUDED.last_attempt_error,
                    updated_at = EXCLUDED.updated_at
                """,
                (committed_at, source_at, committed_at, committed_at),
            )
            finished_at = datetime.now(timezone.utc)
            connection.execute(
                """
                UPDATE ingest_runs SET
                    finished_at = %s,
                    committed_at = %s,
                    source_snapshot_at = %s,
                    repositories_fetched = %s,
                    issues_fetched = %s,
                    pull_requests_fetched = %s,
                    repositories_upserted = %s,
                    issues_upserted = %s,
                    pull_requests_upserted = %s,
                    repositories_deleted = %s,
                    issues_deleted = %s,
                    pull_requests_deleted = %s,
                    data_changed = %s,
                    error = NULL
                WHERE id = %s
                """,
                (
                    finished_at,
                    committed_at,
                    source_at,
                    len(repositories),
                    len(issues),
                    len(pull_requests),
                    repositories_upserted,
                    issues_upserted,
                    pull_requests_upserted,
                    repositories_deleted,
                    issues_deleted,
                    pull_requests_deleted,
                    data_changed,
                    run_id,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return {
        "id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "committed_at": committed_at.isoformat(),
        "source_snapshot_at": source_at.isoformat(),
        "repositories_fetched": len(repositories),
        "issues_fetched": len(issues),
        "pull_requests_fetched": len(pull_requests),
        "repositories_upserted": repositories_upserted,
        "issues_upserted": issues_upserted,
        "pull_requests_upserted": pull_requests_upserted,
        "repositories_deleted": repositories_deleted,
        "issues_deleted": issues_deleted,
        "pull_requests_deleted": pull_requests_deleted,
        "data_changed": data_changed,
        "error": None,
        "skipped": False,
    }


def _post_commit(summary: dict) -> None:
    try:
        cache.response_cache.invalidate()
    except Exception:  # pragma: no cover - a cache hook cannot undo a commit
        log.exception("could not invalidate response cache after ingest")
    try:
        events.broadcast_threadsafe("ingest_done", summary)
    except Exception:  # pragma: no cover - an event hook cannot undo a commit
        log.exception("could not broadcast ingest_done after ingest")


def _post_failure(trigger: str, error: str) -> None:
    """Broadcast durable failure health without exposing source credentials."""
    del error  # The raw detail remains in the durable audit row and logs only.
    try:
        events.broadcast_threadsafe(
            "ingest_failed",
            {
                "trigger": trigger,
                "status": "failure",
                "code": SYNC_FAILURE_CODE,
                "error": SYNC_FAILURE_MESSAGE,
                "at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:  # pragma: no cover - an event hook cannot undo a failure record
        log.exception("could not broadcast ingest_failed")


def run_ingest(trigger: str) -> dict:
    """Acquire and atomically apply one complete public current-state snapshot."""
    with _run_lock_nonblocking() as acquired:
        if not acquired:
            log.warning("ingest (%s) skipped: another run is in flight", trigger)
            return {
                "skipped": True,
                "reason": "ingest already running",
                "trigger": trigger,
            }

        started_at = datetime.now(timezone.utc)
        _set_progress(
            phase="acquiring",
            done=0,
            total=0,
            run_id=None,
            started_at=started_at.isoformat(),
            last_error=None,
        )
        run_id: int | None = None
        try:
            # Opening the audit row is itself fallible (for example during a
            # migration or a database outage). Keep it inside the guarded
            # flow so a failed open cannot strand /health in ``acquiring``.
            run_id = _open_run(started_at, trigger)
            _set_progress(run_id=run_id)
            token, login = _configured_credentials()
            try:
                snapshot = fetch_snapshot(token, login)
            except SourceError:
                raise
            except Exception as exc:
                raise SourceError(f"GitHub snapshot acquisition failed: {exc}") from exc
            source_at, source_login, repositories, issues, pull_requests = _validate_snapshot(
                snapshot
            )
            if source_login.casefold() != login.casefold():
                raise SourceError("snapshot account does not match configured GH_USER")
            _set_progress(
                phase="validated",
                total=len(repositories) + len(issues) + len(pull_requests),
                done=0,
            )
            assert run_id is not None
            summary = _commit_snapshot(
                run_id,
                started_at,
                source_at,
                repositories,
                issues,
                pull_requests,
            )
            summary["trigger"] = trigger
            _post_commit(summary)
            return summary
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            finished_at = datetime.now(timezone.utc)
            if run_id is not None:
                _finish_failed_run(run_id, finished_at, error)
            else:
                _record_failed_attempt(finished_at, error)
                log.exception("could not open ingest run: %s", error)
            _post_failure(trigger, error)
            _reset_progress(error)
            raise
        finally:
            if progress_snapshot()["phase"] != "idle":
                _reset_progress()
