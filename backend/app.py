"""FastAPI entrypoint for the ghpulse dashboard."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
from backend import db  # noqa: E402  # pylint: disable=wrong-import-position

db.load_dotenv(str(_REPO_ROOT / ".env"))

from backend import (  # noqa: E402  # pylint: disable=wrong-import-position
    api,
    cache,
    events,
    ingest,
    login,
    session,
    version,
)
from backend.api_common import (  # noqa: E402  # pylint: disable=wrong-import-position
    DATABASE_UNAVAILABLE_CODE,
    DATABASE_UNAVAILABLE_MESSAGE,
    SYNC_FAILURE_CODE,
    SYNC_FAILURE_MESSAGE,
)

_PUBLIC = _REPO_ROOT / "public"
_SRC = _REPO_ROOT / "src"
_STALE_AFTER_SECONDS = 2 * 60 * 60
_log = logging.getLogger("ghpulse.app")


class _IngestCoordinator:
    """Own ingest calls so teardown can drain workers before closing pools."""

    def __init__(self):
        self._condition = threading.Condition()
        self._active = 0
        self._closing = False

    def run(self, trigger: str) -> dict:
        """Run one ingest unless application teardown has begun."""
        with self._condition:
            if self._closing:
                return {
                    "skipped": True,
                    "reason": "application shutting down",
                    "trigger": trigger,
                }
            self._active += 1
        try:
            return ingest.run_ingest(trigger)
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def begin_shutdown(self) -> None:
        """Reject jobs that have not entered before resource teardown."""
        with self._condition:
            self._closing = True
            self._condition.notify_all()

    @property
    def closing(self) -> bool:
        """Whether this coordinator belongs to a lifespan that has ended."""
        with self._condition:
            return self._closing

    def wait_for_idle(self) -> None:
        """Wait until every admitted ingest has released shared resources."""
        with self._condition:
            while self._active:
                self._condition.wait()


def _run_scheduled_ingest(owner: _IngestCoordinator, trigger: str) -> dict:
    """Run a scheduler-owned ingest without leaking an exception to APScheduler."""
    try:
        return owner.run(trigger)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # run_ingest already records the failed run and health exposes it. The
        # scheduler must not retain an uncaught worker exception while tearing
        # down, nor should it retry a failed complete snapshot implicitly.
        _log.exception("%s ingest failed", trigger)
        return {"skipped": False, "trigger": trigger, "error": str(exc)}


def _asset_hash(path: Path) -> str:
    """Return a content hash suitable for a deterministic cache-bust URL."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _script_json(value: object) -> str:
    """Serialize a value for an inline script without allowing tag escape."""
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _stale_after_seconds() -> float:
    raw = os.environ.get("GHPULSE_STALE_AFTER_SECONDS", str(_STALE_AFTER_SECONDS))
    try:
        return max(0.0, float(raw))
    except ValueError:
        return float(_STALE_AFTER_SECONDS)


def _backend_origin() -> str | None:
    """Return the configured absolute backend origin for the CSP, if any."""
    parsed = urlparse(os.environ.get("BACKEND_URL", "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}{f':{port}' if port else ''}"


def _ingest_progress() -> tuple[bool, dict | None, str | None]:
    progress = ingest.progress_snapshot()
    running = progress.get("phase") not in (None, "idle")
    detail = None
    if running:
        done = int(progress.get("done") or 0)
        total = int(progress.get("total") or 0)
        detail = {
            "phase": progress.get("phase"),
            "done": done,
            "total": total,
            "pct": round(100.0 * done / total, 1) if total else None,
            "run_id": progress.get("run_id"),
            "started_at": progress.get("started_at"),
        }
    return running, detail, progress.get("last_error")


def _health_payload() -> dict:
    """Build health state from both live progress and durable sync metadata."""
    with db.viz_conn() as connection:
        latest_row = connection.execute(
            """
            SELECT id, started_at, finished_at, trigger, committed_at,
                   source_snapshot_at, error
            FROM ingest_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        sync_row = connection.execute(
            """
            SELECT last_committed_at, last_source_snapshot_at
                   , last_attempt_at, last_attempt_status, last_attempt_error
            FROM sync_state
            WHERE id = 1
            """
        ).fetchone()

    running, progress, progress_error = _ingest_progress()
    last_success_value = None
    if sync_row:
        last_success_value = sync_row[0]
    if last_success_value is None and latest_row:
        last_success_value = latest_row[4]

    last_success = _iso(last_success_value)
    sync_status = sync_row[3] if sync_row and sync_row[3] else None
    sync_error = sync_row[4] if sync_row else None
    has_sync_error = bool(
        progress_error
        or sync_error
        or (latest_row and latest_row[6])
        or sync_status == "failure"
    )
    public_error = SYNC_FAILURE_MESSAGE if has_sync_error else None
    stale = True
    if last_success_value is not None:
        if hasattr(last_success_value, "tzinfo"):
            timestamp = last_success_value
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            stale = datetime.now(timezone.utc) - timestamp > timedelta(
                seconds=_stale_after_seconds()
            )
        else:
            stale = False

    last_ingest = None
    if latest_row:
        last_ingest = {
            "id": latest_row[0],
            "started_at": _iso(latest_row[1]),
            "finished_at": _iso(latest_row[2]),
            "trigger": latest_row[3],
            "committed_at": _iso(latest_row[4]),
            "source_snapshot_at": _iso(latest_row[5]),
            "error": public_error if latest_row[6] else None,
            "error_code": SYNC_FAILURE_CODE if latest_row[6] else None,
        }
    return {
        "ok": True,
        "db": True,
        # Which build is answering. The DB-error branch in health() reports
        # it too: "which version is broken" is exactly the question asked
        # when /health is failing, so it must not be the field that goes
        # missing.
        "version": version.VERSION,
        "ingest_running": running,
        "ingest_progress": progress,
        "last_success": last_success,
        "last_error": public_error,
        "last_error_code": SYNC_FAILURE_CODE if public_error else None,
        "stale": stale,
        "sync_status": sync_status,
        "last_attempt": {
            "at": _iso(sync_row[2]) if sync_row else None,
            "status": sync_status,
            "error": SYNC_FAILURE_MESSAGE if has_sync_error else None,
            "code": SYNC_FAILURE_CODE if has_sync_error else None,
        },
        "last_ingest": last_ingest,
        "now": datetime.now(timezone.utc).isoformat(),
    }


async def _cancel_tasks(tasks) -> None:
    """Cancel and drain tasks created for one SSE wait cycle."""
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _event_stream(request: Request):
    """Stream ingest completion events and heartbeat comments to the browser."""

    async def generator():
        queue = events.subscribe()
        shutdown = events.shutdown_event()
        waiters = []
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                if shutdown is not None and shutdown.is_set():
                    break
                waiters = [asyncio.create_task(queue.get())]
                if shutdown is not None:
                    waiters.append(asyncio.create_task(shutdown.wait()))
                done, pending = await asyncio.wait(
                    waiters,
                    timeout=15,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                await _cancel_tasks(pending)
                waiters = []
                if not done:
                    yield ": ping\n\n"
                    continue
                if shutdown is not None and shutdown.is_set():
                    break
                first = next(iter(done), None)
                if first is not None:
                    try:
                        yield first.result()
                    except asyncio.CancelledError:
                        break
        finally:
            await _cancel_tasks(waiters)
            events.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    """Own pools, scheduler, and the event-loop broadcaster as one unit."""
    scheduler = None
    owner = _IngestCoordinator()
    fastapi_app.state.ingest_owner = owner
    try:
        db.open_pools(wait=True)
        db.schema_check()
        cache.start_refresh_workers()
        events.set_loop(asyncio.get_running_loop())

        scheduler = BackgroundScheduler(daemon=True, timezone="UTC")
        scheduler.add_job(
            lambda: _run_scheduled_ingest(owner, "scheduled"),
            "interval",
            hours=1,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
        )
        # APScheduler owns this one-shot worker thread, so a slow GitHub
        # request never blocks the event loop or delays readiness.
        scheduler.add_job(
            lambda: _run_scheduled_ingest(owner, "startup"),
            "date",
            run_date=datetime.now(timezone.utc),
            misfire_grace_time=300,
        )
        scheduler.start()
        fastapi_app.state.scheduler = scheduler
        yield
    finally:
        # Wake SSE consumers before releasing the loop they wait on.
        events.signal_shutdown()
        # Stop admission before stopping APScheduler. A callback already in
        # the executor is counted by owner and is drained below; a callback
        # dequeued during shutdown returns without touching the pools.
        owner.begin_shutdown()
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        # APScheduler's non-waiting shutdown deliberately leaves its current
        # worker alive. Keep the loop responsive while waiting for that worker
        # to finish. There is intentionally no cancellation timeout: Python
        # cannot safely kill a worker in a DB transaction, so closing beneath
        # it would trade a slow shutdown for corrupted resource ownership.
        await asyncio.to_thread(owner.wait_for_idle)
        events.clear_loop()
        cache.stop_refresh_workers()
        # close_pools() is idempotent and also cleans up a partially opened
        # pair if auth-pool opening or schema validation failed.
        db.close_pools()
        fastapi_app.state.ingest_owner = None


app = FastAPI(
    title="ghpulse",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
)


class _SelectiveGZip(GZipMiddleware):  # pylint: disable=too-few-public-methods
    """Compress JSON/static responses without buffering the SSE stream."""

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/api/events":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Attach the browser CSP and related policy headers to every response."""
    response = await call_next(request)
    connect_sources = ["'self'"]
    backend_origin = _backend_origin()
    if backend_origin is not None:
        connect_sources.append(backend_origin)
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "base-uri 'self'; frame-ancestors 'none'; object-src 'none'; "
        "script-src 'self' https://unpkg.com 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com; "
        f"connect-src {' '.join(connect_sources)}; img-src 'self' data:;",
    )
    return response


app.add_middleware(_SelectiveGZip, minimum_size=1024)
app.middleware("http")(session.auth_middleware)
app.include_router(login.router)
app.include_router(api.router)


@app.get("/health")
def health() -> Response:
    """Return database, ingest-progress, and freshness state."""
    try:
        return ORJSONResponse(_health_payload())
    except Exception:  # pylint: disable=broad-exception-caught
        _log.exception("health database check failed")
        return JSONResponse(
            {
                "ok": False,
                "db": False,
                "version": version.VERSION,
                "error": DATABASE_UNAVAILABLE_MESSAGE,
                "error_code": DATABASE_UNAVAILABLE_CODE,
                "now": datetime.now(timezone.utc).isoformat(),
            },
            status_code=503,
        )


@app.post("/admin/ingest")
async def admin_ingest(request: Request) -> dict:
    """Run one complete manual snapshot through the lifespan owner."""
    owner = getattr(request.app.state, "ingest_owner", None)
    if owner is None:
        # Keep direct ASGI/test consumers useful outside a lifespan while
        # production requests always use the lifespan-owned coordinator.
        return await asyncio.to_thread(ingest.run_ingest, "manual")
    return await asyncio.to_thread(owner.run, "manual")


@app.get("/api/events")
async def event_stream(request: Request):
    """Open the ingest-completion SSE stream for the dashboard."""
    return await _event_stream(request)


@app.get("/")
async def root_index(request: Request) -> Response:
    """Serve the cache-busted shell with per-session runtime configuration."""
    html = (_PUBLIC / "index.html").read_text(encoding="utf-8")
    backend_url = os.environ.get("BACKEND_URL", "/")
    is_guest = bool(getattr(request.state, "is_guest", False))
    injection = (
        "<script>window.BACKEND_URL = "
        + _script_json(backend_url)
        + "; window.IS_GUEST = "
        + _script_json(is_guest)
        + ";</script>"
    )
    html = html.replace(
        "<script>window.BACKEND_URL = window.BACKEND_URL || '';</script>\n"
        "<script>window.IS_GUEST = window.IS_GUEST || false;</script>",
        injection,
    )
    html = html.replace(
        'href="/app.css"',
        f'href="/app.css?v={_asset_hash(_PUBLIC / "app.css")}"',
    )
    for path in sorted(_SRC.glob("*")):
        if path.is_file():
            relative = path.relative_to(_SRC).as_posix()
            html = html.replace(
                f'src="/src/{relative}"',
                f'src="/src/{relative}?v={_asset_hash(path)}"',
            )
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/app.css")
async def root_css() -> Response:
    """Serve the dashboard stylesheet with a no-cache response policy."""
    return FileResponse(
        str(_PUBLIC / "app.css"),
        media_type="text/css",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


app.mount("/src", StaticFiles(directory=str(_SRC)), name="src")
