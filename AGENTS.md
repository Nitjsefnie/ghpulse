# ghpulse agent guide

Read `README.md` first. This repository is a narrow statistics dashboard,
not a generic GitHub browser.

## Product invariants

- The initial UI has exactly two panels: external issues and external pull
  requests. Do not add placeholder panels or reintroduce transcript/session
  concepts from claudit.
- Only one configured GitHub account is ingested. Public external repositories
  and items are accepted; private and account-owned records are rejected.
- PostgreSQL current tables are authoritative. Each issue or pull request has
  one current row. Creation and current final outcome are derived at read time;
  no append-only transition/event table may be added.
- Complete source acquisition and pagination must succeed before unseen rows
  are deleted. A failed run preserves the last committed state.
- Current-state changes replace outcomes: a completed issue later reopened has
  no completed outcome until its current state supplies one; a merged PR later
  reopened has no merged outcome.
- Range filters reset every cumulative line to zero at the selected start.
- Guests may view public aggregate data and use range/repository filters.
  Tokens, raw rows, and credentials never enter HTML, frontend JavaScript, or
  API payloads.

## Architecture

`backend/app.py` owns the FastAPI lifespan, startup/hourly complete ingest,
health, admin route, static rewrite, and production middleware. `backend/db.py`
owns separate visualization/auth pools; pool construction must remain explicit
(`open=False`) and opening/closing belongs to lifespan. `backend/ingest.py`
owns the complete current-snapshot transaction. `backend/api_dashboard.py`
derives the two panels from current rows. `vendor/gh-widgets` is a submodule
and remains a standalone SVG renderer; do not edit its contents from ghpulse.

The shell intentionally uses React and Babel from `public/index.html` without a
build step. Served asset URLs receive content hashes. The served page injects
`window.BACKEND_URL` and `window.IS_GUEST` per request using safe JSON; never
inject environment secrets.

## Development and verification

Use test-first changes. Add a focused failing contract before production code,
then run it red and green. Keep backend SQL parameterized and preserve the
separate database boundary. Run the relevant focused tests, then:

```bash
python3 -m pytest tests/ -q
python3 -m pyright
python3 -m pylint backend
python3 /root/.agent-bundle/scripts/ctrlchar_audit.py
```

Run the Bun JSX/Babel contracts and the pinned gh-widgets unittest suite before
claiming a change is complete. Do not weaken tests by adding skips when a CI
dependency is missing. The CI workflow provisions PostgreSQL and Bun.

For deployment, use `examples/ghpulse.service`. Startup and hourly ingestion
are in-process APScheduler jobs. Do not add a second systemd timer or invent
incremental, weekly, resync, or export modes.
