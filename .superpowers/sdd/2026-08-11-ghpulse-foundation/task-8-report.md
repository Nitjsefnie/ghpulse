# Task 8 — Final integration verification

Date: 2026-08-12
Branch: `feature/ghpulse-foundation`
Base: `ca70d3107436cb82d3cda643c340d5296bc72cd6`
Verification code HEAD: `619ea1fc11e9e6f05882786db02f73f5aff2dfba`
Final evidence HEAD: `4716606` (this report commit)

## Result

`DONE_WITH_CONCERNS`: the ghpulse branch is locally verified. The only
remaining failure is external: the exact reviewed gh-widgets submodule object
has not been published to its configured remote, and no push was authorized.
No fallback object, local file remote, moving branch, or substitute SHA was
used.

## Repository and dependency evidence

- `git status --short --branch` → `## feature/ghpulse-foundation`; clean.
- `git submodule update --init --recursive` → exit 0.
- `git submodule status --recursive` →
  `6f7a02c8e1777f17898879be3c31b46d77e61d63 vendor/gh-widgets`.
- `.gitmodules` URL →
  `https://github.com/Nitjsefnie/gh-widgets.git`.
- `git -C vendor/gh-widgets rev-parse HEAD` →
  `6f7a02c8e1777f17898879be3c31b46d77e61d63`.
- `git -C vendor/gh-widgets ls-remote --exit-code origin 6f7a02c8e1777f17898879be3c31b46d77e61d63`
  → exit 2, no matching remote object. The configured remote's `main` ref is
  reachable at `1263f7168a0813501acd9e2d12566947b5a394c9`.

This is the expected portability blocker. A fresh clone can reach the
repository but cannot initialize this exact gitlink until the object is
published.

## Full gates

All commands below ran in the complete local checkout with real PostgreSQL
DSNs (`postgresql:///ghpulse_task8_luna` for visualization and
`postgresql:///ghpulse_task8_auth_luna` for authentication), `COOKIE_SECURE=0`
for local HTTP smoke, and `GHPULSE_BROWSER_SMOKE=1`.

- `python3 -m pytest tests/ -q -ra` → **134 passed in 39.87s**, zero skips.
- `python3 -m pyright` → **0 errors, 0 warnings, 0 informations**.
- `python3 -m pylint backend` → **10.00/10.00**.
- `python3 -m pycodestyle backend tests` → exit 0.
- `python3 -m ruff check backend tests` → **All checks passed**.
- `python3 /root/.agent-bundle/scripts/ctrlchar_audit.py` → 55 tracked files,
  clean, no control characters.
- Bun JSX transpilation/loading-order contract → exit 0; both JSX files
  transpiled and React → Babel → chart → app ordering passed.
- `python3 -m pytest tests/test_app_contract.py tests/test_chart_contract.py -q -ra`
  → **19 passed**.
- `python3 -W error::DeprecationWarning -m pytest tests/test_app.py tests/test_health.py tests/test_browser_smoke.py -q -ra`
  with browser smoke enabled → **14 passed**.
- `python3 -m unittest discover -v` in `vendor/gh-widgets` → **226 tests,
  all OK**.

## End-to-end current-state and browser proof

A disposable PostgreSQL database was reset, `backend/schema.sql` was applied,
and the complete fixture snapshots were ingested through `backend.ingest`.

Initial `snapshot_initial.json` totals:

- Issues: opened 2, completed 1, not planned 0.
- Pull requests: opened 2, merged 0, closed unmerged 1.

After ingesting the changed complete snapshot:

- Issues: opened 1, completed **0**, not planned **1**.
- Pull requests: opened 1, merged **1**, closed unmerged **0**.

Thus the previous final outcomes disappeared and the changed current final
outcomes appeared; no transition/event ledger was consulted.

For API ranges `24h` (`1d`), `7d`, `30d`, `90d`, `1y` (`365d`), and `all`, the
served chart helper was run against the API's dense buckets. Every issue and PR
series had a `{ts: range.start, v: 0, binIdx: -1}` anchor and terminated at
`range.end`.

The same production middleware exercised:

- guest login, `/api/me`, dashboard range and repository filter;
- password login for a real disposable auth user, `/api/me`, and the same
  filtered dashboard path.

The actual Uvicorn-served page was loaded in Chromium for both identities.
React/Babel/chart/app loading completed with no console errors, page errors,
request failures, or CSP violations. Each identity rendered exactly two
`section.panel-shell` panels: External Issues and External Pull Requests.
The EventSource abort caused by full-page navigation was excluded as an
intentional transport cancellation; the new cancellation regression verifies
that it leaves no async waiter tasks behind.

## Verified defect fixed

The integration browser run reproduced pending `Queue.get` and `Event.wait`
tasks after `/api/events` generator cancellation. The cause was that the
generator cancelled neither its `asyncio.wait` children during cancellation
nor its finally path; it only unsubscribed the queue. The focused test was
written first and failed with both waiter tasks still live. Commit
`619ea1fc11e9e6f05882786db02f73f5aff2dfba` adds a minimal cancel-and-gather
helper for each wait cycle and generator teardown. The focused pair then
passed, followed by the full 134-test suite and strict browser smoke.

Changed files in that fix commit: `backend/app.py`, `tests/test_app.py`.

## Documentation, CI, operations, and privacy inspection

- `README.md`, `AGENTS.md`, and `CONTRIBUTING.md` consistently describe one
  account, public external-only data, PostgreSQL current-state rows, complete
  reconciliation, guest/authenticated aggregate access, exactly two panels,
  and no raw/private/token surfaces.
- `.github/workflows/tests.yml` provisions PostgreSQL 16 and Bun, initializes
  recursive submodules, rejects pytest skips, runs browser/quality gates, and
  runs the vendored suite. Its fresh checkout remains subject to the
  unpublished exact submodule object above.
- `examples/ghpulse.service` contains one service only; startup and hourly
  complete ingest are in-process APScheduler jobs. No weekly timer, resync, or
  incremental mode is present.
- `git diff --name-status ca70d31..HEAD` shows 19 intentional implementation
  commits plus this evidence artifact commit and the exact submodule gitlink;
  no edits were made inside the vendored checkout.
- `/api/dashboard`, served HTML, and frontend JavaScript contain no raw rows,
  GitHub token, or credentials; guest access is limited to public aggregate
  routes.

## History audit

Canonical command:

```text
python3 /root/.agent-bundle/scripts/author_stats.py --list ca70d31..HEAD
```

Result after the evidence commit: **20 commits, 20 co-authored, 0 missing**.
The 19 implementation commits and this report commit each have exactly one
`Co-Authored-By: GPT-5.6 Luna <noreply@openai.com>` trailer. No history was
rewritten and neither repository was pushed.
