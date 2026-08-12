# Task 8 — Final integration verification

Date: 2026-08-12
Branch: `feature/ghpulse-foundation`
Base: `ca70d3107436cb82d3cda643c340d5296bc72cd6`
Evidence capture: `e6828665571a4d20ea241d0f6e8b7fec71a313a3`

## Status

`DONE_WITH_CONCERNS`

The ghpulse checkout is locally verified. The only remaining concern is
external: the exact reviewed gh-widgets gitlink is not published to its
configured GitHub remote. No push was authorized, and no fallback object,
local-file remote, reference clone, alternate object database, moving branch,
or substitute SHA was used.

The evidence range below ends at the latest implementation/fix commit. The
Markdown and HTML report artifacts were written and committed immediately
afterward; they are documentation artifacts and are intentionally excluded
from that implementation/fix range so the commit list is not self-referential.

## Repository and submodule evidence

- `git status --short --branch` before report authoring → `## feature/ghpulse-foundation`; clean.
- `git submodule update --init --recursive` → exit 0.
- `git submodule status --recursive` → `6f7a02c8e1777f17898879be3c31b46d77e61d63 vendor/gh-widgets (heads/main-16-g6f7a02c)`.
- `git -C vendor/gh-widgets rev-parse HEAD` → `6f7a02c8e1777f17898879be3c31b46d77e61d63`.
- `git -C vendor/gh-widgets status --short` → empty.
- `.gitmodules` and the submodule remote both identify `https://github.com/Nitjsefnie/gh-widgets.git`.
- `git -C vendor/gh-widgets ls-remote origin refs/heads/main` → `1263f7168a0813501acd9e2d12566947b5a394c9 refs/heads/main`.

### Isolated remote-only fetch

The following was run in a newly initialized temporary repository with only
the configured HTTPS remote. It had no alternates file and did not borrow the
local checkout's object database:

```text
git -C "$remote_tmp" init -q
git -C "$remote_tmp" remote add origin https://github.com/Nitjsefnie/gh-widgets.git
git -C "$remote_tmp" -c protocol.version=0 fetch --no-tags origin 6f7a02c8e1777f17898879be3c31b46d77e61d63
fatal: remote error: upload-pack: not our ref 6f7a02c8e1777f17898879be3c31b46d77e61d63
fatal: the remote end hung up unexpectedly
isolated_repo=/tmp/tmp.N43Jk9Bo42
alternates_present=no
fetch_exit=128
```

The command asserted a nonzero fetch exit and `alternates_present=no`, so the
result is a genuine remote publication failure rather than a local lookup.

## Full verification gates

All PostgreSQL-backed commands used the disposable visualization DSN
`postgresql:///ghpulse_task8_luna` and authentication DSN
`postgresql:///ghpulse_task8_auth_luna`, with `COOKIE_SECURE=0`,
`ADMIN_TOKEN=task8-integration-admin`, and `GHPULSE_BROWSER_SMOKE=1`.

| Gate | Exact command/result |
| --- | --- |
| Full ghpulse suite | `env GHPULSE_TEST_DATABASE_URL=postgresql:///ghpulse_task8_luna DATABASE_URL_VIZ=postgresql:///ghpulse_task8_luna DATABASE_URL_AUTH=postgresql:///ghpulse_task8_auth_luna GHPULSE_BROWSER_SMOKE=1 COOKIE_SECURE=0 ADMIN_TOKEN=task8-integration-admin python3 -W error::DeprecationWarning -m pytest tests/ -q -ra` → **135 passed in 29.72s; 0 skipped**. |
| Final real integration | Same DSN environment with `pytest tests/test_final_integration.py -q -ra` → **1 passed in 16.34s**. |
| Existing browser smoke | `env GHPULSE_BROWSER_SMOKE=1 python3 -W error::DeprecationWarning -m pytest tests/test_browser_smoke.py -q -m browser -ra` → **1 passed in 6.41s**. |
| App/chart contracts | `python3 -W error::DeprecationWarning -m pytest tests/test_app_contract.py tests/test_chart_contract.py -q -ra` → **19 passed in 1.22s**. |
| pycodestyle | `python3 -m pycodestyle backend tests` → exit 0. |
| Pylint | `python3 -m pylint backend` → **10.00/10.00**. |
| Pyright | `python3 -m pyright` → **0 errors, 0 warnings, 0 informations**. |
| Ruff | `python3 -m ruff check backend tests` → **All checks passed**. |
| Bun | CI-equivalent `Bun.Transpiler` checks for both JSX files and React → Babel → chart → app loading order → **Bun JSX transpilation/loading order passed**. |
| Control characters | `python3 /root/.agent-bundle/scripts/ctrlchar_audit.py` → **58 tracked files, clean**. |
| gh-widgets vendor | `python3 -m unittest discover -v` from `vendor/gh-widgets` → **Ran 226 tests; OK**. |

## End-to-end current-state proof

`tests/test_final_integration.py` is checked in and executable. It requires a
real `GHPULSE_TEST_DATABASE_URL` and fails, rather than skips, when that DSN is
absent. The test applies `backend/schema.sql`, creates a disposable password
user in the auth database, starts the production FastAPI app through Uvicorn,
waits for the real startup complete ingest, and drives it with Chromium.

The initial complete snapshot produced issues opened 2, completed 1, not
planned 0; pull requests opened 2, merged 0, closed unmerged 1. The changed
complete snapshot produced issues opened 1, completed 0, not planned 1; pull
requests opened 1, merged 1, closed unmerged 0. Therefore old final outcomes
disappeared and new current outcomes appeared. The proof uses current
PostgreSQL rows, not a transition ledger. The changed fixture removes
repository `R_2`; the repository API and `repository=R_1` dashboard filter
verify complete reconciliation.

The browser proof exercises both guest and password-authenticated identities
through production middleware, `/api/me`, dashboard and repository APIs, and
the admin ingest route. For every user-facing range — `24h` (`1d`), `7d`,
`30d`, `90d`, `1y` (`365d`), and `all` — every issue and PR cumulative series
has a `{ts: range.start, v: 0, binIdx: -1}` anchor and terminates at
`range.end`. Both identities render exactly two `section.panel-shell` panels.
The harness records console errors, page errors, failed requests, and CSP
violations; all four collections are empty for both identities. The GitHub
fixture token is absent from served page content.

The final test uses no pool, ingest, API, or session mocks. Its source input is
the explicit `GHPULSE_TEST_SOURCE_SNAPSHOT` test selector in
`backend/github_source.py`; that selector calls the existing pinned
gh-widgets `load_snapshot()` path through `load_snapshot_file()`, then applies
the consumer's public/external validation. With the selector absent, the
production path still calls pinned `fetch_authored_snapshot()`. The only
browser route is for the non-functional Google Fonts stylesheet: the mutable
CDN CSS response is fulfilled as an empty successful stylesheet so an external
font URL cannot create a false dashboard request failure. Production HTML,
CSP, static assets, PostgreSQL, ingest, APIs, auth, middleware, Uvicorn,
React, Babel, and chart code remain real.

## Verified evidence defects and fixes

1. Commit `704b88b1444acf389e9c1d5fd2fb026e4fca43db` added the checked-in real
   integration gate and CI step. CI supplies real PostgreSQL/auth DSNs, admin
   settings, and strict deprecation warnings; the full-suite skip guard remains.
2. The first focused browser run found a concrete external dependency failure:
   the production Google Fonts stylesheet requested a WOFF2 URL that returned
   404/`net::ERR_ABORTED`. Commit `e6828665571a4d20ea241d0f6e8b7fec71a313a3`
   isolates that optional stylesheet request in the test context. The focused
   real integration gate then passed without changing production behavior.

## Documentation, CI, service, and privacy drift inspection

- `README.md`, `AGENTS.md`, and `CONTRIBUTING.md` agree on one configured
  account, exactly two panels, public external-only current-state rows, guest
  and authenticated aggregate access, complete reconciliation, and a
  server-only GitHub token.
- `.github/workflows/tests.yml` checks out recursive submodules, provisions
  PostgreSQL and Bun, applies visualization/auth schemas, supplies real DSNs
  to both all-tests and final-integration steps, rejects skipped tests, runs
  strict browser/static/frontend/vendor gates, and has no local fallback for
  the unpublished gitlink.
- `examples/ghpulse.service` has one service and startup/hourly paths use
  complete ingest. No weekly, incremental, resync, export, or second-account
  mode is present.
- Source and API inspection confirms the token stays server-side; the browser
  receives aggregate public responses only. The two dashboard panels are the
  only rendered panel shells.

## Ordered commit evidence

Exact output of `git log --reverse --format='%H %s' ca70d31..e682866` (22
commits, in order):

```text
03960304724f85a697859796ba0770035b69745e feat: bootstrap the ghpulse application shell
06cd7bc31f6d8441c9a76d752248903b7bf469c8 test: strengthen ghpulse shell contracts
5d125f4d3e5eb57f40293b6edfe9e379d15083c4 feat: add GitHub source and current-state schema
7e7e6d7da611e662a4919b06a73cf4f8123ad31a fix: keep ghpulse adapter on public widget APIs
b97c239cf0eb6b678ea7389eb9f48844b0407096 fix: validate fetched GitHub data in memory
7e2c1a0a31828b89cfcd13fcb5f558d47d5208e9 feat: ingest current GitHub activity state
af176430a3f6dc61d1fc89a3b9482de936c51bf9 fix: harden ghpulse ingest failures and schema migration
544d938e9a69b8f1da9ddeaf2feeeb62a994073a feat: serve external activity timelines
e969407c927e1fe547074de26809bcb10aef174f fix: keep dashboard reads snapshot-consistent
c421268495b2805f8f694c5326e7c02f0b3e67bb feat: add stacked cumulative timeline chart
516247075a4700f624823c959581f6716aeaa8c9 fix: preserve dense timeline intervals and chart geometry
6b81222c06d4f607a44cd9b5ff024738eb214331 fix: require complete dense timeline coverage
a8c7f0cbc7f0d8f2b9380dc72fdfb19939c307d4 feat: build the two-panel ghpulse dashboard
6e4ac7f11a27607d3e65e0b9091bb391d08e5ed5 fix: preserve ghpulse dashboard selection state
ebf064616b2c33fc5196b33e78d2b0fc243f3c22 test: prove ghpulse refresh and chart contracts
cd58ba059530b9cdad8d034db852866d6715984f feat: complete ghpulse operations and lifecycle
adf692230ccc035f721020afdeb7db23ea64472d fix: close ghpulse lifecycle safely
5a6b52b68b0e1007a5c96c798c615276459249b1 test: make browser smoke warning-free
619ea1fc11e9e6f05882786db02f73f5aff2dfba fix: clean up cancelled SSE waiters
3f155af20ca7bf4de07caec5c5bca7e0f34fbe1e docs: record final integration verification
704b88b1444acf389e9c1d5fd2fb026e4fca43db test: add reproducible final integration gate
e6828665571a4d20ea241d0f6e8b7fec71a313a3 test: isolate external font asset in browser proof
```

Canonical coauthor audit at the evidence capture range:

```text
python3 /root/.agent-bundle/scripts/author_stats.py --list ca70d31..e682866
```

Result: **22 commits, 22 co-authored, 0 missing**; each commit carries one
`Co-Authored-By: GPT-5.6 Luna <noreply@openai.com>` trailer. No history was
rewritten and neither repository was pushed.

## Report artifact note

This Markdown report and its adjacent HTML companion are committed after the
evidence capture above. The final clean-status and coauthor audit were rerun
after those documentation artifacts were committed; the explicit evidence
range remains `ca70d31..e682866` to avoid claiming a self-referential report
commit as implementation evidence.
