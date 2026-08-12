"""Current-state aggregate implementation for ``GET /api/dashboard``."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from starlette.requests import Request

from backend import db
from backend.api_common import (
    Phases,
    RangeWindow,
    _bucket_seconds,
    _iso,
    _parse_range,
    as_utc,
    dense_bucket_bounds,
    read_transaction,
    response_bucket,
    utc_now,
)
from backend.cache import cache_response


router = APIRouter()

_ISSUE_KEYS = ("opened", "completed", "not_planned")
_PR_KEYS = ("opened", "merged", "closed_unmerged")


def _repository_predicate(repository: str | None) -> tuple[str, list[Any]]:
    if repository is None:
        return "", []
    return " AND repository_id = %s", [repository]


def _event_union() -> str:
    """Return the static, current-state event union.

    A mutable item contributes its creation event and at most one final-state
    outcome.  There is no transition ledger, so reopening/reclassifying a row
    naturally removes the previous outcome from this query.
    """
    return """
        SELECT i.repository_id, i.created_at AS event_at, 'issues' AS panel,
               'opened' AS kind
        FROM issues i
        JOIN repositories r ON r.node_id = i.repository_id
        WHERE r.is_private IS FALSE AND r.is_external IS TRUE
          AND i.is_private IS FALSE
        UNION ALL
        SELECT i.repository_id, i.closed_at AS event_at, 'issues' AS panel,
               'completed' AS kind
        FROM issues i
        JOIN repositories r ON r.node_id = i.repository_id
        WHERE r.is_private IS FALSE AND r.is_external IS TRUE
          AND i.is_private IS FALSE
          AND i.state = 'CLOSED' AND i.state_reason = 'COMPLETED'
          AND i.closed_at IS NOT NULL
        UNION ALL
        SELECT i.repository_id, i.closed_at AS event_at, 'issues' AS panel,
               'not_planned' AS kind
        FROM issues i
        JOIN repositories r ON r.node_id = i.repository_id
        WHERE r.is_private IS FALSE AND r.is_external IS TRUE
          AND i.is_private IS FALSE
          AND i.state = 'CLOSED' AND i.state_reason = 'NOT_PLANNED'
          AND i.closed_at IS NOT NULL
        UNION ALL
        SELECT p.repository_id, p.created_at AS event_at, 'pull_requests' AS panel,
               'opened' AS kind
        FROM pull_requests p
        JOIN repositories r ON r.node_id = p.repository_id
        WHERE r.is_private IS FALSE AND r.is_external IS TRUE
          AND p.is_private IS FALSE
        UNION ALL
        SELECT p.repository_id, p.merged_at AS event_at, 'pull_requests' AS panel,
               'merged' AS kind
        FROM pull_requests p
        JOIN repositories r ON r.node_id = p.repository_id
        WHERE r.is_private IS FALSE AND r.is_external IS TRUE
          AND p.is_private IS FALSE
          AND p.state = 'MERGED' AND p.merged IS TRUE
          AND p.merged_at IS NOT NULL
        UNION ALL
        SELECT p.repository_id, p.closed_at AS event_at, 'pull_requests' AS panel,
               'closed_unmerged' AS kind
        FROM pull_requests p
        JOIN repositories r ON r.node_id = p.repository_id
        WHERE r.is_private IS FALSE AND r.is_external IS TRUE
          AND p.is_private IS FALSE
          AND p.state = 'CLOSED' AND p.merged IS FALSE
          AND p.closed_at IS NOT NULL
    """


def _event_bounds(connection, repository: str | None) -> datetime | None:
    repo_pred, repo_args = _repository_predicate(repository)
    query = f"""
        WITH event_union AS ({_event_union()})
        SELECT MIN(event_at)
        FROM event_union
        WHERE event_at IS NOT NULL {repo_pred}
    """
    row = connection.execute(db.sql_text(query), repo_args).fetchone()
    return as_utc(row[0]) if row and row[0] is not None else None


def build_window(
    rng: str,
    repository: str | None = None,
    *,
    now=None,
    connection=None,
) -> RangeWindow:
    """Resolve a requested range to a UTC half-open window."""
    delta = _parse_range(rng)
    end = as_utc(now or utc_now())
    if rng == "all":
        if connection is None:
            with read_transaction() as read_connection:
                return build_window(
                    rng, repository, now=end, connection=read_connection
                )
        start = _event_bounds(connection, repository)
        if start is None:
            return RangeWindow(rng, end, end, _bucket_seconds(delta))
        span = end - start
        return RangeWindow(rng, start, end, _bucket_seconds(span))
    start = end - delta
    return RangeWindow(rng, start, end, _bucket_seconds(delta))


def validate_repository(connection, repository: str | None) -> None:
    """Reject unknown, private, or non-external repository identifiers."""
    if repository is None:
        return
    row = connection.execute(
        """
        SELECT 1
        FROM repositories
        WHERE node_id = %s AND is_private IS FALSE AND is_external IS TRUE
        """,
        (repository,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="repository not found")


def _event_rows(connection, window: RangeWindow, repository: str | None, phases: Phases):
    repo_pred, repo_args = _repository_predicate(repository)
    query = f"""
        WITH event_union AS ({_event_union()})
        SELECT FLOOR(EXTRACT(EPOCH FROM event_at) / %s)::bigint * %s AS bucket_epoch,
               panel,
               kind,
               COUNT(*)::bigint AS n
        FROM event_union
        WHERE event_at IS NOT NULL
          AND event_at >= %s
          AND event_at < %s
          {repo_pred}
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
    """
    args: list[Any] = [window.bucket_s, window.bucket_s, window.start, window.end]
    args.extend(repo_args)
    cursor = phases.execute("events", connection, db.sql_text(query), args)
    return cursor.fetchall()


def _summary_open_counts(connection, window: RangeWindow, repository: str | None, phases: Phases):
    repo_pred, repo_args = _repository_predicate(repository)
    query = f"""
        SELECT
          (SELECT COUNT(*)::bigint
           FROM issues i JOIN repositories r ON r.node_id = i.repository_id
           WHERE r.is_private IS FALSE AND r.is_external IS TRUE
             AND i.is_private IS FALSE AND i.state = 'OPEN'
             AND i.created_at >= %s AND i.created_at < %s
             {repo_pred.replace('repository_id', 'i.repository_id')}) AS issues_open,
          (SELECT COUNT(*)::bigint
           FROM pull_requests p JOIN repositories r ON r.node_id = p.repository_id
           WHERE r.is_private IS FALSE AND r.is_external IS TRUE
             AND p.is_private IS FALSE AND p.state = 'OPEN'
             AND p.created_at >= %s AND p.created_at < %s
             {repo_pred.replace('repository_id', 'p.repository_id')}) AS prs_open
    """
    args = [window.start, window.end, *repo_args, window.start, window.end, *repo_args]
    cursor = phases.execute("open_summary", connection, db.sql_text(query), args)
    row = cursor.fetchone()
    return (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)


def _repository_rows(connection, repository_ids: list[str]) -> list[dict]:
    if not repository_ids:
        return []
    rows = connection.execute(
        """
        SELECT node_id, name_with_owner, url
        FROM repositories
        WHERE node_id = ANY(%s)
          AND is_private IS FALSE AND is_external IS TRUE
        ORDER BY name_with_owner
        """,
        (repository_ids,),
    ).fetchall()
    return [
        {"node_id": node_id, "name_with_owner": name, "url": url}
        for node_id, name, url in rows
    ]


def _last_ingest(connection) -> str | None:
    row = connection.execute(
        """
        SELECT last_committed_at
        FROM sync_state
        WHERE id = 1
        """
    ).fetchone()
    return _iso(row[0]) if row else None


def _fold_buckets(
    rows,
    window: RangeWindow,
    keys: tuple[str, ...],
) -> tuple[list[dict], dict[str, int]]:
    by_epoch: dict[int, dict[str, int]] = {}
    totals = {key: 0 for key in keys}
    for epoch, _panel, kind, count in rows:
        epoch = int(epoch)
        if kind not in keys:
            continue
        bucket = by_epoch.setdefault(epoch, {key: 0 for key in keys})
        bucket[kind] += int(count or 0)
        totals[kind] += int(count or 0)

    buckets = []
    for start, end, epoch in dense_bucket_bounds(window.start, window.end, window.bucket_s):
        buckets.append(response_bucket(start, end, by_epoch.get(epoch, {key: 0 for key in keys})))
    return buckets, totals


def _event_repository_ids(connection, window: RangeWindow, repository: str | None) -> list[str]:
    repo_pred, repo_args = _repository_predicate(repository)
    query = f"""
        WITH event_union AS ({_event_union()})
        SELECT DISTINCT repository_id
        FROM event_union
        WHERE event_at IS NOT NULL AND event_at >= %s AND event_at < %s {repo_pred}
        ORDER BY repository_id
    """
    args: list[Any] = [window.start, window.end, *repo_args]
    rows = connection.execute(db.sql_text(query), args).fetchall()
    return [str(row[0]) for row in rows]


def _dashboard_build(connection, window: RangeWindow, repository: str | None) -> dict:
    phases = Phases("dashboard")
    started = utc_now()
    with phases.step("sql"):
        rows = _event_rows(connection, window, repository, phases)
        issue_rows = [row for row in rows if row[1] == "issues"]
        pr_rows = [row for row in rows if row[1] == "pull_requests"]
        issue_buckets, issue_totals = _fold_buckets(issue_rows, window, _ISSUE_KEYS)
        pr_buckets, pr_totals = _fold_buckets(pr_rows, window, _PR_KEYS)
        issue_open, pr_open = _summary_open_counts(connection, window, repository, phases)
        repository_ids = _event_repository_ids(connection, window, repository)
        repositories = _repository_rows(connection, repository_ids)
        last_ingest = _last_ingest(connection)

    phases.done(
        issues=len(issue_buckets),
        pull_requests=len(pr_buckets),
        repositories=len(repositories),
    )
    return {
        "range": window.name,
        "bucket_s": window.bucket_s,
        "start": _iso(window.start),
        "end": _iso(window.end),
        "issues": issue_buckets,
        "pull_requests": pr_buckets,
        "summary": {
            "repositories": len(repositories),
            "issues": {
                **issue_totals,
                "currently_open": issue_open,
            },
            "pull_requests": {
                **pr_totals,
                "currently_open": pr_open,
            },
            "last_ingest": last_ingest,
        },
        "repositories": repositories,
        "generated_at": _iso(started),
    }


@cache_response
def dashboard(
    rng: str = Query("30d", alias="range"),
    repository: str | None = Query(None),
    visibility: str = "guest",
    fresh: int = Query(0),
) -> dict:
    """Return dense current-state issue and PR event timelines.

    ``visibility`` is deliberately part of this function's cache key even
    though both supported viewers currently see the same public aggregates.
    That prevents a future authenticated-only field from being served through
    a guest cache entry by accident.
    """
    del visibility  # retained as a cache-key boundary, never serialized
    del fresh  # cache_response handles the bypass before invoking us
    with read_transaction() as connection:
        validate_repository(connection, repository)
        window = build_window(rng, repository, connection=connection)
        return _dashboard_build(connection, window, repository)


@router.get("/dashboard")
def dashboard_route(
    request: Request,
    rng: str = Query("30d", alias="range"),
    repository: str | None = Query(None),
    fresh: int = Query(0),
) -> dict:
    visibility = "guest" if bool(getattr(request.state, "is_guest", False)) else "authenticated"
    return dashboard(
        rng=rng,
        repository=repository,
        visibility=visibility,
        fresh=fresh,
    )
