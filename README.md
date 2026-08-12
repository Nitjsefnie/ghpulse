# ghpulse

**ghpulse** is a self-hosted, one-account GitHub activity dashboard. It
displays public activity in external repositories using the same dark shell,
spacing, chart geometry, and interaction conventions as claudit.

The first release intentionally has exactly two panels:

- External Issues — opened, completed, and not planned;
- External Pull Requests — opened, merged, and closed unmerged.

Each panel has stacked interval bars and separate cumulative series. Cumulative
series reset to zero at the beginning of the selected range. The dashboard
stores one current row per GitHub node: creation is plotted from `created_at`,
and at most one outcome is plotted from the current final state. If GitHub
changes an item's state, its old outcome disappears on the next complete
snapshot and the new outcome appears. There is no event-history ledger.

## Scope and relationship to gh-widgets

ghpulse owns PostgreSQL persistence, authentication, complete reconciliation,
aggregate APIs, and the dashboard. It reads acquisition and normalization from
the pinned `vendor/gh-widgets` submodule. `gh-widgets` remains independently
runnable and remains the SVG renderer for its own widget commands; ghpulse does
not render widget SVGs and does not use renderer-private cache files as an API.

Only public, external repositories and items are ingested for one configured
GitHub account. Account-owned repositories, private records, and incomplete
source pages are rejected. GitHub credentials remain server-side and are never
injected into HTML or returned by an API.

## Setup

Requires Python 3.13+, PostgreSQL 16+, and a checked-out `gh-widgets` submodule.

```bash
git clone https://github.com/Nitjsefnie/ghpulse.git
cd ghpulse
git submodule update --init --recursive

createdb ghpulse
psql ghpulse -f backend/schema.sql

python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example .env
# Edit DATABASE_URL_VIZ, DATABASE_URL_AUTH, ADMIN_TOKEN, GH_USER, and GH_TOKEN.
# Keep BACKEND_URL as the same-origin path prefix (normally /).

python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

`DATABASE_URL_VIZ` points to the ghpulse database. `DATABASE_URL_AUTH` points
to the separate users database whose `users.config` JSONB contains the PBKDF2
web-password fields. The pools never join across databases. Reapplying
`backend/schema.sql` is idempotent.

Production acquisition uses the pinned gh-widgets public data API. `GH_USER`
and `GH_TOKEN` identify the one source account. `GH_EXTRA_INSIDERS` is reserved
for the source adapter's existing configuration and does not add another
dashboard account. `BACKEND_URL` is injected into each served page as the
same-origin path prefix used by the browser for API and SSE requests.

## Authentication and guest access

Login uses a numeric user ID and password from the external auth database.
Sessions are HMAC-signed, `HttpOnly`, `SameSite=Strict` cookies with a seven-day
TTL. The login page also offers **Continue as guest**. Authenticated and guest
sessions may view public aggregate panels and use range and repository filters.
Guest sessions cannot access future raw/export surfaces. Admin ingestion is
separate and requires `X-Admin-Token` plus a same-origin `Origin` or `Referer`.

## Ingest and operations

Every ingest is a complete current snapshot. It validates the full source,
upserts all public external rows, deletes unseen rows only inside the successful
transaction, updates `sync_state`, invalidates stale aggregate responses, and
broadcasts `ingest_done` to connected browsers. A process-local non-blocking
lock makes an overlapping run return a truthful `skipped` result.

The FastAPI lifespan opens both pools, starts one startup complete ingest in an
APScheduler worker, and runs the same complete ingest hourly. Shutdown signals
SSE clients, stops new scheduler admission, drains every in-flight complete
ingest while its pools remain open, clears the broadcaster, and only then
closes both pools. There is no separate timer, weekly sync, resync mode,
incremental mode, export mode, or fabricated full-vs-incremental distinction.
The pinned GitHub transport uses a 20-second request timeout with three
transient retries; the service example allows 90 seconds for a bounded worker
to drain. Python threads are never force-cancelled underneath a transaction.

For operations, see [`examples/ghpulse.service`](examples/ghpulse.service).

```bash
curl -X POST http://127.0.0.1:8000/admin/ingest \
  -H "Origin: http://127.0.0.1:8000" \
  -H "X-Admin-Token: $ADMIN_TOKEN"

curl http://127.0.0.1:8000/health
systemctl restart ghpulse
journalctl -u ghpulse -f
```

`/health` reports database availability, live phase/progress, last successful
commit, the latest error, and whether the last successful commit is stale
(default threshold: two hours; configure `GHPULSE_STALE_AFTER_SECONDS`).

## Tests

The full suite uses fixture-backed source data. PostgreSQL integration tests
run when `GHPULSE_TEST_DATABASE_URL` is set; CI provisions it and does not allow
those tests to be skipped.

```bash
python3 -m pytest tests/ -q
python3 -m pyright
python3 -m pylint backend
python3 -m pycodestyle backend tests
python3 -m ruff check backend tests
```

The canonical control-character audit is a local/release gate and is not
invoked by hosted CI (the script is supplied by the agent bundle, not this
repository):

```bash
python3 /root/.agent-bundle/scripts/ctrlchar_audit.py --repo . --strict
```

Frontend contracts use the real `src/dashboard-charts.jsx` and `src/app.jsx`
through the browser's Babel loading order. Bun is used for JSX parsing and
transpilation checks. `vendor/gh-widgets` has its own stdlib unittest suite:

```bash
python3 -m unittest discover -v -s vendor/gh-widgets
```

Run `git submodule update --init --recursive` before tests in a fresh checkout.
The checked-in submodule commit is part of the ghpulse deploy and must remain
the tested public API version.

## Layout

- `backend/` — FastAPI lifecycle, auth, ingest, API, cache, SSE, and schema;
- `public/` — the cache-busted HTML/CSS shell;
- `src/` — the two-panel React/Babel modules;
- `vendor/gh-widgets/` — pinned standalone acquisition/normalization and SVG
  renderer;
- `tests/` — backend, source-boundary, lifecycle, and frontend contracts;
- `examples/` — systemd service example.
