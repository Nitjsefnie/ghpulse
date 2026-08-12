# ghpulse Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-ready one-account GitHub dashboard with Claudit's proven application shell and two external issue/PR outcome timelines.

**Architecture:** Pin the verified gh-widgets public data API as a submodule, ingest normalized current-state snapshots into PostgreSQL, and expose two adaptively bucketed aggregate series through FastAPI. Copy Claudit's proven auth, cache, SSE, styling, and bounded chart geometry, extending only the time-series renderer to support stacked bars and three cumulative lines.

**Tech Stack:** Python 3.13+, FastAPI, Uvicorn, psycopg3 pools, APScheduler, orjson, PostgreSQL 16+, React 18 via CDN, in-browser Babel, vanilla JSX/SVG, pytest, Node-based frontend contract tests.

## Global Constraints

- Product/repository name is `ghpulse`; the current checkout directory may be renamed later.
- One configured GitHub account; no GitHub OAuth and no per-viewer account.
- Public external repositories only; gh-widgets uses the account login and explicitly public organisations transiently during acquisition and never serializes membership relationships.
- Login plus guest access; both may use range and repository filters.
- Initial release has exactly two panels: issues and pull requests.
- Current state only: old final outcomes disappear when GitHub's current state changes.
- Outcome events use current `closed_at` or `merged_at`; creation events use `created_at`.
- Three stacked interval series and three separately cumulative lines per panel; cumulative values reset to zero at the selected range start.
- Use Claudit's proven CSS, shell, range handling, response cache, SSE behavior, and bounded chart geometry; do not redesign them.
- No raw item inspector, private data, scoring, qualitative analysis, or placeholder panels.
- Test-first implementation and a local commit after each task.

## File Structure

- `.gitmodules`, `vendor/gh-widgets/`: exact verified upstream pin.
- `backend/app.py`: lifespan, scheduled ingest, health, admin ingest, static serving.
- `backend/api_common.py`: range, adaptive bucket, timestamps, timing helpers.
- `backend/api.py`: `/api/me`, `/api/repositories`, `/api/events`.
- `backend/api_dashboard.py`: aggregate SQL and response contract for two panels.
- `backend/github_source.py`: narrow adapter around `vendor/gh-widgets/ghwidgets_data.py`.
- `backend/ingest.py`: locked complete-snapshot sync, transactional state upsert/reconciliation, cache invalidation, and SSE notification.
- `backend/schema.sql`: repositories, issues, pull_requests, ingest_runs, sync_state.
- Claudit-derived `backend/auth.py`, `login.py`, `session.py`, `db.py`, `events.py`, `cache.py`.
- `public/index.html`, `public/app.css`: proven shell and visual tokens.
- `src/app.jsx`: controls, summary, loading/error/SSE flow, exactly two panels.
- `src/dashboard-charts.jsx`: shared helpers and stacked/cumulative chart.
- `tests/`: fixture-driven backend, auth, ingest, API, geometry, and source-contract tests.

---

### Task 1: Bootstrap the proven application shell

**Files:**
- Create: `backend/__init__.py`, `backend/requirements.txt`, `backend/.env.example`
- Create: `backend/auth.py`, `backend/login.py`, `backend/session.py`, `backend/db.py`, `backend/events.py`, `backend/cache.py`
- Create: `public/index.html`, `public/app.css`
- Create: `tests/conftest.py`, `tests/test_auth.py`, `tests/test_login.py`, `tests/test_session.py`, `tests/test_cache.py`, `tests/test_events.py`
- Create: `pyrightconfig.json`, `setup.cfg`, `.gitignore`

**Interfaces:**
- Produces: `viz_conn()` and independent `auth_conn()`/pool lifecycle.
- Produces: signed session-cookie middleware with authenticated and guest identities.
- Produces: thread-safe SSE broadcaster and stale-while-refresh response cache.
- Produces: static asset shell with `window.BACKEND_URL` and `window.IS_GUEST` injection.

- [ ] **Step 1: Copy Claudit's behavioral tests before implementation**

Port the auth, login, session, event, and cache tests, changing only product/database names and allowing guest access to aggregate public endpoints. Preserve PBKDF2 vectors, cookie flags, origin checks, cache stale-serving, and event-loop shutdown cases.

- [ ] **Step 2: Run the tests and verify imports fail**

Run: `python3 -m pytest tests/test_auth.py tests/test_login.py tests/test_session.py tests/test_cache.py tests/test_events.py -q`  
Expected: collection failures because backend modules do not exist.

- [ ] **Step 3: Port the focused backend modules**

Copy the proven Claudit implementations without unrelated transcript logic. Configure defaults:

```text
DATABASE_URL_VIZ=postgresql:///ghpulse
DATABASE_URL_AUTH=postgresql:///auth
SESSION_COOKIE=ghpulse_session
COOKIE_SECURE=1
```

Keep visualization/auth pools separate and never join across databases.

- [ ] **Step 4: Port the static shell and asset cache-busting contract**

Copy Claudit's index/CSS tokens and its backend rewrite behavior. Change visible branding only to `ghpulse`; do not alter spacing, panel chrome, typography, responsive breakpoints, or tooltip classes.

- [ ] **Step 5: Run focused tests**

Run: `python3 -m pytest tests/test_auth.py tests/test_login.py tests/test_session.py tests/test_cache.py tests/test_events.py -q`  
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend public tests pyrightconfig.json setup.cfg .gitignore
git commit -m "feat: bootstrap the ghpulse application shell"
```

---

### Task 2: Pin gh-widgets and define current-state persistence

**Files:**
- Create: `.gitmodules`, `vendor/gh-widgets/`
- Create: `backend/github_source.py`
- Create: `backend/schema.sql`
- Create: `tests/test_github_source.py`, `tests/test_schema.py`
- Create: `tests/fixtures/github_snapshot.json`

**Interfaces:**
- Consumes: gh-widgets `SCHEMA_VERSION`, `fetch_authored_snapshot`, `load_snapshot`, `write_snapshot`.
- Produces: `fetch_snapshot(token: str, login: str) -> dict` and `load_snapshot_file(path: str) -> dict`.
- Produces tables: `repositories`, `issues`, `pull_requests`, `ingest_runs`, `sync_state`.

- [ ] **Step 1: Add the exact verified submodule pin**

Run:

```bash
ghwidgets_sha=6f7a02c8e1777f17898879be3c31b46d77e61d63
git submodule add https://github.com/Nitjsefnie/gh-widgets.git vendor/gh-widgets
git -C vendor/gh-widgets fetch /root/gh-widgets "$ghwidgets_sha"
git -C vendor/gh-widgets checkout "$ghwidgets_sha"
```

Do not track a moving branch in `.gitmodules`. The final portability gate
requires this exact upstream commit to be present on the configured remote;
publishing it is an external action and is performed only with explicit push
authorization.

- [ ] **Step 2: Write failing adapter and schema tests**

```python
def test_adapter_rejects_private_records(snapshot_path):
    body = github_source.load_snapshot_file(snapshot_path)
    assert all(not row["is_private"] for row in body["issues"])

def test_schema_has_current_state_not_transition_log(schema_text):
    assert "CREATE TABLE IF NOT EXISTS issues" in schema_text
    assert "state_reason" in schema_text
    assert "issue_events" not in schema_text
```

- [ ] **Step 3: Run tests and verify failure**

Run: `python3 -m pytest tests/test_github_source.py tests/test_schema.py -q`  
Expected: imports/files missing.

- [ ] **Step 4: Implement the narrow source adapter**

Load the submodule module by a deterministic repository-relative path, validate its schema version at import, and expose only the two functions above. Reject any private row defensively even though upstream normalizes public-only data.

- [ ] **Step 5: Implement idempotent schema**

Use GitHub node IDs as primary keys. Repository foreign keys cascade to items. Add indexes on repository, created, updated, closed, and merged timestamps. `sync_state` is a singleton row with the last committed/source snapshot timestamps; `ingest_runs` records trigger, counts, timestamps, and error.

- [ ] **Step 6: Apply schema twice in a disposable database**

Run: `createdb ghpulse_test_schema && psql ghpulse_test_schema -f backend/schema.sql && psql ghpulse_test_schema -f backend/schema.sql`  
Expected: both applications succeed and table definitions are unchanged.

- [ ] **Step 7: Run tests and commit**

Run: `python3 -m pytest tests/test_github_source.py tests/test_schema.py -q`  
Expected: all pass.

```bash
git add .gitmodules vendor/gh-widgets backend/github_source.py backend/schema.sql tests
git commit -m "feat: add GitHub source and current-state schema"
```

---

### Task 3: Complete current-state ingest

**Files:**
- Create: `backend/ingest.py`
- Modify: `backend/schema.sql`
- Create: `tests/test_ingest.py`
- Create: `tests/fixtures/snapshot_initial.json`, `tests/fixtures/snapshot_changed.json`

**Interfaces:**
- Consumes: normalized snapshot dictionaries and visualization DB pool.
- Produces: `run_ingest(trigger: str) -> dict`.
- Produces: `progress_snapshot() -> dict`.

- [ ] **Step 1: Write failing cold-sync and state-switch tests**

```python
def test_changed_issue_replaces_final_state(run_with_snapshots, db_rows):
    run_with_snapshots("snapshot_initial.json")
    run_with_snapshots("snapshot_changed.json")
    issue = db_rows("SELECT state, state_reason, closed_at FROM issues WHERE node_id='I_1'")[0]
    assert issue.state_reason == "NOT_PLANNED"

def test_failed_complete_sync_never_deletes_unseen(existing_rows, failing_source):
    with pytest.raises(SourceError):
        ingest.run_ingest("test")
    assert existing_rows() == 3
```

- [ ] **Step 2: Run tests and verify ingest is missing**

Run: `python3 -m pytest tests/test_ingest.py -q`  
Expected: import failure.

- [ ] **Step 3: Implement transactional upserts**

Upsert repositories first, then issues and PRs. Every mutable field is replaced from the current snapshot. Reject any record that is not already public and external even though the pinned upstream producer enforces that boundary. Record per-run counts.

- [ ] **Step 4: Implement safe complete reconciliation and sync state**

Every run consumes one complete validated snapshot. Collect seen IDs and delete unseen rows only in the same transaction after the entire source fetch and every upsert succeed. Record the committed/source snapshot timestamps only on success.

- [ ] **Step 5: Implement locking and post-commit cache/SSE hooks**

Use Claudit's non-blocking ingest lock semantics. PostgreSQL is authoritative; do not create a second post-commit snapshot artifact. After a data-changing commit, invalidate response caches and broadcast `ingest_done`. A concurrent invocation returns a truthful skipped summary.

- [ ] **Step 6: Run ingest tests**

Run: `python3 -m pytest tests/test_ingest.py -q`  
Expected: cold sync, idempotency, state change, complete reconciliation, failed rollback, external-owner changes, cache invalidation, and SSE notification all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/ingest.py backend/schema.sql tests/test_ingest.py tests/fixtures
git commit -m "feat: ingest current GitHub activity state"
```

---

### Task 4: Aggregate dashboard API

**Files:**
- Create: `backend/api_common.py`, `backend/api.py`, `backend/api_dashboard.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Produces: `GET /api/me`.
- Produces: `GET /api/repositories?range=<range>`.
- Produces: `GET /api/dashboard?range=<range>&repository=<node-id>`.
- Produces response keys: `range`, `bucket_s`, `issues`, `pull_requests`, `summary`, `repositories`, `generated_at`.

- [ ] **Step 1: Write failing API contract tests**

```python
def test_current_state_moves_issue_outcome(client, changed_issue_rows):
    body = client.get("/api/dashboard?range=all").json()
    assert sum(b["completed"] for b in body["issues"]) == 0
    assert sum(b["not_planned"] for b in body["issues"]) == 1

def test_guest_can_filter_public_repository(guest_client, repository_id):
    response = guest_client.get(f"/api/dashboard?range=30d&repository={repository_id}")
    assert response.status_code == 200
```

- [ ] **Step 2: Run tests and verify routes are missing**

Run: `python3 -m pytest tests/test_api.py -q`  
Expected: 404/import failures.

- [ ] **Step 3: Port range/bucket helpers from Claudit**

Preserve supported ranges and adaptive bounded buckets. `range=all` starts at the earliest creation/outcome timestamp. Reject unknown ranges and unknown/private/non-external repository IDs with 422/404 as appropriate.

- [ ] **Step 4: Implement event-union aggregate SQL**

For each item table, construct a SQL union of creation and one current outcome predicate, then group by epoch-aligned bucket and repository. Return dense zero-filled buckets to the frontend with issue keys `opened`, `completed`, `not_planned` and PR keys `opened`, `merged`, `closed_unmerged`.

- [ ] **Step 5: Implement summaries and caching**

Summaries follow selected range/repository. Currently open counts current-open rows created inside the range. Wrap dashboard responses in the stale-while-refresh cache using a key containing user visibility, range, and repository.

- [ ] **Step 6: Run API tests and commit**

Run: `python3 -m pytest tests/test_api.py -q`  
Expected: all pass, including empty data, boundaries, guest, repository, and state-switch cases.

```bash
git add backend/api_common.py backend/api.py backend/api_dashboard.py tests/test_api.py
git commit -m "feat: serve external activity timelines"
```

---

### Task 5: Stacked interval and cumulative chart

**Files:**
- Create: `src/dashboard-charts.jsx`
- Create: `tests/test_time_series_geometry.py`, `tests/test_chart_contract.py`

**Interfaces:**
- Produces: `StackedCumulativeTimeSeriesPanel({title, events, series, range, binMs})`.
- `series` entries: `{key: string, label: string, color: string}`.
- Produces pure helper: `buildStackedTimeSeriesData(events, series, range, binMs) -> {bins, cumulative, totals}`.

- [ ] **Step 1: Port Claudit geometry tests and add three-series failures**

Test exact bounded intervals, final partial bin, stacked sums, one zero anchor per cumulative series, range-edge x coordinates, and totals:

```javascript
const out = buildStackedTimeSeriesData(events, series, {start:0,end:400}, 120);
assert.deepEqual(out.totals, {opened:3, completed:1, not_planned:1});
assert.deepEqual(out.cumulative.opened[0], {ts:0,v:0,binIdx:-1});
assert.equal(out.bins.at(-1).end, 400);
```

- [ ] **Step 2: Run tests and verify component/helpers are missing**

Run: `python3 -m pytest tests/test_time_series_geometry.py tests/test_chart_contract.py -q`  
Expected: failures for missing source symbols.

- [ ] **Step 3: Port the proven component and generalize data only**

Retain Claudit's resize observer, measured label gutters, time ticks, bounded bar widths, hover crosshair, tooltip positioning, and panel chrome. Replace the one-value bin with per-series sums; stack rects bottom-up. Render three cumulative polylines against one right-axis maximum.

- [ ] **Step 4: Make the tooltip explicit**

For each series show `period` and `cumulative`, followed by the interval's total events and percentage of that series' selected-range total. Never label the sum of creations plus outcomes as unique items.

- [ ] **Step 5: Run geometry tests and commit**

Run: `python3 -m pytest tests/test_time_series_geometry.py tests/test_chart_contract.py -q`  
Expected: all pass.

```bash
git add src/dashboard-charts.jsx tests/test_time_series_geometry.py tests/test_chart_contract.py
git commit -m "feat: add stacked cumulative timeline chart"
```

---

### Task 6: Two-panel dashboard application

**Files:**
- Create: `src/app.jsx`
- Modify: `public/index.html`, `public/app.css`
- Create: `tests/test_app_contract.py`

**Interfaces:**
- Consumes: `/api/me`, `/api/repositories`, `/api/dashboard`, `/api/events`.
- Produces: range and repository controls, summary strip, issue panel, PR panel, loading/empty/error states.

- [ ] **Step 1: Write failing source-contract tests**

Assert the app renders exactly two `StackedCumulativeTimeSeriesPanel` calls titled `External Issues` and `External Pull Requests`, includes range/repository controls and summary labels, and contains no copied transcript panels or local upload path.

- [ ] **Step 2: Run contract tests and verify app is missing**

Run: `python3 -m pytest tests/test_app_contract.py -q`  
Expected: missing `src/app.jsx`.

- [ ] **Step 3: Port Claudit's shell and fetch lifecycle**

Preserve URL/query state, loading overlay, cache-phase display behavior, SSE reconnect/refetch logic, responsive grids, guest indicator, and logout behavior. Replace model/project vocabulary with repository vocabulary.

- [ ] **Step 4: Render the exact initial surface**

Issue series: Opened, Completed, Not planned. PR series: Opened, Merged, Closed unmerged. Use consistent colors between stacked bars, lines, legends, summary values, and tooltips. Do not create hidden or placeholder panels.

- [ ] **Step 5: Run frontend tests and commit**

Run: `python3 -m pytest tests/test_app_contract.py tests/test_time_series_geometry.py tests/test_chart_contract.py -q`  
Expected: all pass.

```bash
git add src/app.jsx public tests/test_app_contract.py
git commit -m "feat: build the two-panel ghpulse dashboard"
```

---

### Task 7: Application lifecycle, operations, and full verification

**Files:**
- Create: `backend/app.py`
- Create: `examples/ghpulse.service`, `examples/ghpulse-resync.service`
- Create: `README.md`, `AGENTS.md`, `CONTRIBUTING.md`
- Create: `.github/workflows/tests.yml`
- Modify: `backend/.env.example`
- Create/modify: `tests/test_app.py`, `tests/test_health.py`

**Interfaces:**
- Produces: startup ingest, hourly complete-snapshot scheduler, `/health`, protected `POST /admin/ingest`, static application routes.
- Produces: service examples for hourly in-process sync and weekly explicit full resync.

- [ ] **Step 1: Write failing lifecycle and health tests**

Cover startup scheduling, non-blocking shutdown with SSE clients, health before/after success and error, admin token constant-time check, origin rejection, static asset injection, and cache-bust query strings.

- [ ] **Step 2: Run lifecycle tests and verify app module is missing**

Run: `python3 -m pytest tests/test_app.py tests/test_health.py -q`  
Expected: import failure.

- [ ] **Step 3: Port Claudit lifecycle behavior**

Mount auth/API routers, schedule hourly `run_ingest("scheduled")`, launch startup ingest outside the event loop, report progress/last run through health, and shut down scheduler/pools/broadcaster deterministically.

- [ ] **Step 4: Add deployment and repository documentation**

Document scope, setup, PostgreSQL schema, environment, submodule initialization/update, standalone gh-widgets relationship, manual/full ingest, auth/guest behavior, tests, and the current-state event invariant. Service examples must use explicit paths and timeouts and must never expose the GitHub token to the frontend.

- [ ] **Step 5: Run the complete verification suite**

Run: `python3 -m pytest tests/ -q`  
Run: `python3 -m pyright`  
Run: `python3 -m pylint backend`  
Run: `node --check` through the repository's JSX source-contract harness  
Run: `python3 /root/.agent-bundle/scripts/ctrlchar_audit.py` against tracked files  
Expected: every command exits 0.

- [ ] **Step 6: Run a local fixture-backed smoke test**

Apply `backend/schema.sql` to a fresh test database, ingest the fixture snapshot, start Uvicorn, and verify `/health`, login/guest, `/api/repositories`, `/api/dashboard`, and `/` return expected responses. Confirm exactly two panels appear in the served source.

- [ ] **Step 7: Commit**

```bash
git add backend/app.py backend/.env.example examples README.md AGENTS.md CONTRIBUTING.md .github tests
git commit -m "feat: complete ghpulse operations and lifecycle"
```

---

### Task 8: Final integration verification

**Files:**
- Modify only for verified defects.

**Interfaces:**
- Produces: a clean, deployable ghpulse branch pinned to a verified gh-widgets commit.

- [ ] **Step 1: Initialize submodules from a clean clone/worktree**

Run: `git submodule update --init --recursive`  
Expected: exact pinned `vendor/gh-widgets` SHA and clean status.

- [ ] **Step 2: Run all ghpulse and upstream compatibility gates**

Run ghpulse's full verification from Task 7 and `python3 -m unittest discover -v` inside `vendor/gh-widgets`. Both suites must pass in the same checkout.

- [ ] **Step 3: Verify current-state chart behavior end to end**

Ingest the initial fixture, record the issue/PR API totals, ingest the changed fixture, and confirm the previous outcome bucket decrements while the new outcome bucket increments. Confirm all cumulative lines restart at zero for `24h`, `7d`, `30d`, `90d`, `1y`, and `all`.

- [ ] **Step 4: Verify worktree and commit history**

Run: `git status --short`, `git log --format='%h %s'`, and the co-author audit. Expected: clean worktree, intentional task commits, and required model trailer on every implementation commit.
