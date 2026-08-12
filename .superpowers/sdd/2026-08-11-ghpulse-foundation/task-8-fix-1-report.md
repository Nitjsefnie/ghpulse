# Task 8 fix round 1 report

**Status:** DONE_WITH_CONCERNS

## What was implemented

- Added checked-in `tests/test_final_integration.py`, an executable real-boundary
  integration test using disposable PostgreSQL, the production source adapter
  and ingest, Uvicorn, production middleware, Chromium, guest and password
  sessions, repository filtering, all six ranges, zero anchors/end points,
  exactly two panels, and console/page/request/CSP assertions.
- Added the CI step with real PostgreSQL/auth DSNs and strict deprecation
  warnings. Existing full-suite skip rejection remains enabled.
- Added the explicit test-only `GHPULSE_TEST_SOURCE_SNAPSHOT` selector inside
  `backend/github_source.py`. It uses the pinned gh-widgets loader and the
  existing public/external validation; production continues to call the real
  `fetch_authored_snapshot()` path when the selector is absent.
- Isolated the mutable, non-functional Google Fonts stylesheet request in the
  final browser context after a real 404/`ERR_ABORTED` was observed. No pool,
  ingest, API, or session behavior is mocked.
- Replaced the invalid `ls-remote <SHA>` availability claim with an isolated
  remote-only fetch that records `upload-pack: not our ref`, `fetch_exit=128`,
  and `alternates_present=no`.
- Corrected the adjacent Task 8 Markdown/HTML report and recorded the exact
  ordered `ca70d31..e682866` implementation/fix commit list.

Commits: `704b88b` (integration gate), `e682866` (external font test
isolation). Both carry `Co-Authored-By: GPT-5.6 Luna <noreply@openai.com>`.

## Tests and evidence

- Real-DSN strict full suite: **135 passed, 0 skipped**.
- Focused final production integration: **1 passed**.
- Existing strict Chromium smoke: **1 passed**.
- App/chart contracts: **19 passed**.
- Pylint: **10.00/10.00**; Pyright: **0 errors**; pycodestyle and Ruff:
  exit 0 / all checks passed.
- Bun JSX/loading-order contract: passed.
- Canonical control-character audit: **58 tracked files, clean**.
- Vendored gh-widgets suite: **226 tests, OK**.
- Exact submodule pin: `6f7a02c8e1777f17898879be3c31b46d77e61d63`; checkout clean.
- Configured remote `main`: `1263f7168a0813501acd9e2d12566947b5a394c9`.
- Isolated fetch: exact remote error `fatal: remote error: upload-pack: not our ref 6f7a02c8e1777f17898879be3c31b46d77e61d63`; no alternates; exit 128.

The full evidence transcript and ordered commit list are in
[`task-8-report.md`](task-8-report.md), with the HTML companion
`task-8-report.html`.

## Self-review

The final test does not replace application pools, ingest, APIs, sessions, or
browser application assets. The source fixture selector is deterministic and
keeps the same checked-in gh-widgets load/validation boundary. The only
remaining concern is external publication of the exact gh-widgets object; no
push or fallback was used.

## Files changed

- `.github/workflows/tests.yml`
- `backend/github_source.py`
- `tests/test_final_integration.py`
- `.superpowers/sdd/2026-08-11-ghpulse-foundation/task-8-report.md`
- `.superpowers/sdd/2026-08-11-ghpulse-foundation/task-8-report.html`
- `.superpowers/sdd/2026-08-11-ghpulse-foundation/task-8-fix-1-report.md`
- `.superpowers/sdd/2026-08-11-ghpulse-foundation/task-8-fix-1-report.html`

Neither repository was pushed. The report artifacts are committed separately
after the implementation/fix evidence range, so the ordered list remains
explicit and non-self-referential.
