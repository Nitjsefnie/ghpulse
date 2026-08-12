# Task 7 fix round 1 report

## Status

DONE

## Implemented

- Added a lifespan-owned `_IngestCoordinator`. Startup, hourly, and manual
  ingestion are admitted through the coordinator; shutdown stops admission,
  asks APScheduler to stop dispatching, drains every admitted worker without
  closing PostgreSQL pools underneath it, then clears SSE state and closes
  pools. Scheduler callbacks convert failed runs into logged health-visible
  results instead of leaking worker exceptions.
- Added a real APScheduler regression using a deliberately blocked startup
  worker. It proves the worker finishes before pool closure and that the real
  scheduler receives no job error.
- Removed the hosted-CI call to the agent-bundle control-character script.
  The canonical command remains documented as a local/release gate in the
  README and contributor instructions.
- Deleted the project-wide `.pylintrc` and restored the pycodestyle 100-column
  policy. Existing complexity/type checks use module-local, documented
  suppressions only; Pylint, pycodestyle, Ruff, and Pyright pass.
- Added `tests/test_browser_smoke.py`, a real Uvicorn + Playwright Chromium
  smoke that follows guest login, executes CDN/SRI/Babel/CSP loading, verifies
  exactly two mounted panels/SVGs, and fails on console, page, resource-load,
  or CSP violations. CI pins Playwright/Ruff and installs Chromium
  deterministically.
- Corrected README shutdown wording to document the drain-before-close order.

## Verification

- `python3 -m pytest tests/test_app.py tests/test_health.py -q -W error::DeprecationWarning` — 12 passed.
- `GHPULSE_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/ghpulse_task7_test GHPULSE_BROWSER_SMOKE=1 DATABASE_URL_AUTH=postgresql://postgres:postgres@127.0.0.1:5432/auth_test python3 -m pytest tests/ -q -ra` — 133 passed, 2 warnings (third-party Uvicorn/websockets deprecations emitted by the browser smoke).
- `GHPULSE_BROWSER_SMOKE=1 python3 -m pytest tests/test_browser_smoke.py -q -m browser` — 1 passed.
- Bun JSX/transpiler and script-order contract — passed.
- `python3 -m pyright` — 0 errors, 0 warnings, 0 informations.
- `python3 -m pylint backend --score=no` — passed.
- `python3 -m pycodestyle backend tests` — passed.
- `python3 -m ruff check backend tests` with pinned Ruff 0.11.8 — passed.
- `python3 /root/.agent-bundle/scripts/ctrlchar_audit.py --repo . --strict` — clean.
- `python3 -m unittest discover -v` in `vendor/gh-widgets` — 226 passed.
- `git diff --check` — clean.

## Files changed

`backend/app.py`, `backend/api.py`, `backend/api_common.py`,
`backend/api_dashboard.py`, `backend/auth.py`, `backend/cache.py`,
`backend/db.py`, `backend/events.py`, `backend/ingest.py`,
`backend/login.py`, `backend/session.py`, `setup.cfg`, `README.md`,
`.github/workflows/tests.yml`, `tests/test_app.py`,
`tests/test_app_contract.py`, `tests/test_browser_smoke.py`,
`tests/test_chart_contract.py`, `tests/test_ingest.py`,
`tests/test_time_series_geometry.py`; removed `.pylintrc`.

## Concerns

The full PostgreSQL run used the existing local disposable databases. Fresh
clone portability remains blocked only by the unpublished gh-widgets pin, as
specified by the task; no push or workaround was attempted.
