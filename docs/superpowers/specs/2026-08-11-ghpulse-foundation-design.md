# ghpulse foundation and first two timelines

**Date:** 2026-08-11  
**Status:** Approved by standing user direction  
**Scope:** A one-account GitHub activity dashboard with the proven Claudit application shell and two initial panels

## Context

`ghpulse` is a self-hosted dashboard for one configured GitHub account. It must look and behave like Claudit rather than inventing a new dashboard system: FastAPI, PostgreSQL, React 18 loaded from CDN, in-browser Babel, the existing dark visual system, adaptive time buckets, responsive SVG charts, login plus guest sessions, response caching, scheduled ingest, and SSE refresh.

The initial release deliberately has only two panels:

1. External issues and their current outcomes.
2. External pull requests and their current outcomes.

More panels may be added later, but this design does not build empty abstractions or placeholder panels for them.

## Decision

Build `ghpulse` on Claudit's proven backend/frontend conventions and pin `gh-widgets` as a read-only submodule. Refactor `gh-widgets` minimally upstream so its GitHub acquisition and normalization are a supported importable API while its existing CLIs remain independently runnable. `ghpulse` owns PostgreSQL persistence and dashboard aggregation; `gh-widgets` remains the standalone SVG renderer.

The boundary between them is a versioned normalized snapshot contract, not either renderer's private cache layout. `ghpulse` imports the public acquisition/normalization module. Every `gh-widgets` renderer continues fetching, caching, and rendering through its existing standalone path; the dashboard does not feed snapshots into widget renderers.

## Goals

- Preserve `gh-widgets` as a fully standalone application.
- Reuse GitHub identity, pagination, external-repository classification, and normalization between both products.
- Reuse Claudit's exact application shell, visual tokens, chart geometry, filtering behavior, cache strategy, authentication model, and SSE refresh behavior.
- Fetch only public authored issues and pull requests for one configured GitHub account.
- Show repository and time-range filters from the first release.
- Treat stored items as current snapshots, not an append-only state-transition history.
- Keep the first release to two useful panels and a small summary strip.

## Non-goals

- Rendering `gh-widgets` SVG cards inside `ghpulse`.
- Reimplementing Claudit's styling or chart geometry.
- GitHub OAuth or per-viewer GitHub accounts.
- Qualitative analysis, coaching, AI narratives, or issue/PR scoring.
- A raw issue/PR browser, search page, or detail inspector in the first release.
- Additional panels such as languages, streaks, stars, responsiveness, impact, reviews, or code ownership.
- Treating private renderer cache files as a public integration API.

## Architecture

### Repository relationship

`ghpulse` contains `vendor/gh-widgets` as a Git submodule pinned to an exact commit. The pin changes deliberately through a normal submodule update; it never follows the upstream branch implicitly at runtime.

`gh-widgets` gains a small stable module for:

- fetching the configured token identity and organisation memberships;
- deriving the insider owner set and exact author identity;
- paging authored public issues and pull requests without silent truncation;
- normalizing repositories, issues, and pull requests into provider-neutral dictionaries;
- reading and writing the versioned snapshot atomically.

The public module is an import boundary for pinned consumers. Existing renderer commands keep their current behavior, cache durability, CLI flags, and output unchanged; adding snapshot modes to those commands would couple a narrow dashboard interchange schema to renderer-private profile and impact data that it does not contain.

### ghpulse backend

The backend follows Claudit's split rather than growing a single route module:

- `backend/app.py`: lifespan, hourly scheduled ingest, router mounting, static assets, cache-busted index rewriting, health, and admin ingest.
- `backend/api.py`: identity, repository list, SSE events, shared range/bucket parsing.
- `backend/api_dashboard.py`: the two timeline queries and response assembly.
- `backend/auth.py`, `login.py`, `session.py`, `db.py`, `events.py`, and `cache.py`: adapted from Claudit with the same separation of the visualization and external authentication databases.
- `backend/github_source.py`: the only adapter to the pinned `gh-widgets` acquisition API.
- `backend/ingest.py`: sync planning, upserts, full-resync reconciliation, snapshot export, cache invalidation, and SSE notification.
- `backend/schema.sql`: current repository/item state and ingest metadata.

### ghpulse frontend

The frontend uses Claudit's existing `public/index.html`, `public/app.css`, layout structure, theme values, filtering controls, summary cells, tooltip grammar, and responsive SVG approach. It copies the proven components into this repository so deployment does not depend on a sibling checkout.

The existing bounded time-series geometry is generalized only as far as required: one panel accepts three named series, renders stacked interval bars on the left axis, and renders one cumulative line per series on the right axis. The established range-edge, partial-final-bin, label-measurement, tooltip, resize, and plot-boundary behavior remains unchanged and receives regression tests.

## Identity and external-repository rule

The dashboard is for one configured account (`GH_USER`) and one GitHub token. A repository is external when its owner is neither the account login nor any organisation returned for that token, plus additive `GH_EXTRA_INSIDERS`. This is the same rule as `gh-widgets`.

Identity and organisations are refreshed on every successful sync. If the insider set changes, all stored repositories are reclassified from their owner login without refetching each item. Only public repositories enter the dashboard or exported snapshot.

## Current-state data model

### repositories

- stable GitHub node ID;
- `owner/name` full name and owner login;
- URL;
- visibility/private flag;
- current external classification;
- first/last observed timestamps.

### issues

- stable GitHub node ID and repository foreign key;
- issue number and URL;
- `created_at`, current `updated_at`, and current `closed_at`;
- current state (`OPEN` or `CLOSED`);
- current state reason (`COMPLETED`, `NOT_PLANNED`, or null).

### pull_requests

- stable GitHub node ID and repository foreign key;
- PR number and URL;
- `created_at`, current `updated_at`, current `closed_at`, and current `merged_at`;
- current state (`OPEN`, `CLOSED`, or `MERGED`) and merged boolean.

### ingest_runs and sync_state

Ingest runs record trigger, start/finish, fetched/upserted/deleted counts, whether the pass was full, and errors. Sync state records the last successful high-water timestamp. Incremental passes overlap that timestamp so an interrupted page or equal timestamp cannot create a gap.

No state-transition table exists. Each GitHub node has one current row. Upserting a changed row removes its former derived outcome automatically because dashboard events are derived from the current columns at query time.

## Event semantics

Each item may contribute one creation event and at most one current outcome event.

| Panel | Series | Event timestamp | Current-state predicate |
|---|---|---|---|
| Issues | Opened | `created_at` | every stored external issue |
| Issues | Completed | current `closed_at` | `state=CLOSED` and `state_reason=COMPLETED` |
| Issues | Not planned | current `closed_at` | `state=CLOSED` and `state_reason=NOT_PLANNED` |
| Pull requests | Opened | `created_at` | every stored external PR |
| Pull requests | Merged | current `merged_at` | currently merged |
| Pull requests | Closed unmerged | current `closed_at` | currently closed and not merged |

Open is not an event series. It is derived for summaries as opened minus successful and unsuccessful current outcomes.

If an issue is completed, reopened, and later closed as not planned, the completed event disappears and a not-planned event appears at the current `closed_at`. If a closed-unmerged PR is reopened and merged, its closed-unmerged event disappears and a merged event appears at `merged_at`. Mid-states are never retained or counted.

## Time-series behavior

Each of the two panels contains:

- stacked per-interval bars for all three event series;
- three matching cumulative lines;
- a left axis for per-bucket event counts;
- a right axis for cumulative counts;
- a tooltip showing the interval value and cumulative value for every series;
- the same adaptive bucket selection and bounded range geometry as Claudit.

Cumulative values reset to zero at the selected range's left edge. They count only events whose event timestamp falls inside that selected range. They do not carry a lifetime baseline into a shorter range.

The repository filter applies to both creation and outcome events. The range options and URL/query behavior follow Claudit.

## Summary strip

The header summary remains intentionally small:

- external repositories represented in the selected range;
- issues opened / completed / not planned / currently open;
- PRs opened / merged / closed unmerged / currently open;
- last successful ingest time and stale/error indication.

Counts obey the selected repository and time range. "Currently open" is computed from current state among items created inside the selected range; it is not an event count.

## Fetch and reconciliation

### Cold/full sync

A cold start and explicit/weekly resync page the entire public authored issue and PR history. Pagination must continue until `hasNextPage` is false. A safety ceiling may abort loudly but must never return a silently partial dataset.

The full pass marks every seen node. After all pages succeed, unseen stored nodes are removed: they are no longer visible, public, authored by the account, or returned by GitHub. Deletion happens only after a completely successful full pass.

### Hourly incremental sync

The hourly pass fetches items updated since an overlap before the last successful high-water timestamp and upserts them. The high-water mark advances only after every page succeeds. Incremental sync never deletes unseen rows.

The hourly job runs under a non-blocking process lock. A concurrent tick exits cleanly. Per-object/page failures are recorded and leave the high-water mark unchanged so the next run retries.

### Derived state and response caching

Dashboard aggregation reads current normalized tables directly for the first release; the item volume is small enough that premature rollup tables are not justified. The response cache uses Claudit's stale-while-refresh behavior. After a data-changing ingest it is invalidated, and connected clients receive `ingest_done` over SSE.

If measured query cost later justifies rollups, they can be added behind the same API response contract.

## Snapshot contract for gh-widgets

The exported JSON is a presentation input, not a copy of PostgreSQL and not a renderer cache. It contains:

- `schema_version`;
- `generated_at` and source account identity;
- current insiders;
- normalized public external repositories;
- normalized current issues;
- normalized current pull requests.

It is written atomically and validated through the shared `gh-widgets` module. Missing future sections are explicit and do not silently fall back to stale data. The contract is consumed by ghpulse's ingest adapter, not by the SVG renderer CLIs; every standalone renderer continues using its existing fetch/cache path and private renderer-specific data.

## Authentication and guest behavior

Reuse Claudit's external auth database, PBKDF2 verification, signed `HttpOnly` session cookie, origin checks, login rate limiting, and guest sentinel. The configured GitHub token is server-only and never reaches the browser.

Both authenticated and guest sessions may view aggregate panels and use the range and repository filters because the underlying GitHub data is public. Admin ingest remains protected by `X-Admin-Token`. There is no raw token, cache, or database endpoint.

## Error handling

- GraphQL errors and incomplete pagination fail the sync rather than committing a partial reconciliation.
- A failed incremental sync preserves current rows, current high-water state, and the last good dashboard response.
- A failed full sync never deletes unseen rows.
- A corrupt or incompatible exported snapshot is rejected by the public `gh-widgets` data API; standalone renderers remain independent of that interchange file.
- Unknown issue state reasons remain stored but do not enter either final-outcome series until explicitly mapped.
- Missing outcome timestamps exclude that outcome event and surface an ingest warning; timestamps are never guessed.
- Health reports the last successful ingest, current progress, and most recent error.

## Testing

### Shared gh-widgets API

- Existing standalone renderer tests remain green.
- Snapshot round-trip and schema-version rejection are covered.
- Pagination cannot silently truncate.
- External-owner classification matches existing renderer behavior.

### Backend

- Fixture-driven GraphQL pages cover cold sync, incremental overlap, full reconciliation, and failure rollback.
- State changes prove old derived outcomes disappear and new outcomes appear once.
- Repository-owner/organisation changes reclassify existing rows.
- API tests cover range, repository filter, guest access, adaptive buckets, empty data, and current-open summaries.
- Authentication/session/admin behavior retains Claudit's security tests.

### Frontend

- Stacked bars sum correctly per interval.
- Each cumulative series starts at zero and ends at its in-range total.
- Final partial buckets end exactly at the selected range edge.
- Bars never cross plot bounds; right-axis labels do not collide with titles.
- Tooltips report all interval and cumulative series values.
- Responsive behavior and empty/loading/error states are covered through the existing Node-based JSX test approach.

### Gates

- Full pytest suite.
- Python formatting, lint, and type checks matching the source repositories.
- JavaScript syntax and source-contract tests.
- Control-character and secrecy audits.
- A real local run against a fixture GitHub source and PostgreSQL test database.

## Deployment

Ship a systemd service behind nginx, following Claudit's deployment pattern. Startup triggers an ingest; APScheduler runs hourly incremental sync; a separate timer or service argument performs the weekly full resync. PostgreSQL schema application is idempotent. Environment includes visualization/auth DSNs, cookie/admin secrets, GitHub user/token settings, extra insiders, snapshot output path, and parser/source version.

The submodule commit is part of every deploy. Deployment fails if it is missing or its public API/schema version does not match `ghpulse`.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Current-state rewrites make historical charts change | This is the explicit product rule; fixtures make the behavior visible and deterministic. |
| GitHub pagination caps or API cost | Complete cursor pagination, incremental overlap, cache reuse, and loud failure instead of truncation. |
| `gh-widgets` and dashboard normalization drift | One importable upstream module, a pinned submodule commit, and snapshot contract tests on both sides. |
| Claudit UI fixes diverge after copying | Preserve component boundaries and regression tests; selectively port proven fixes rather than inventing replacements. |
| Full resync removes data after a partial fetch | Reconciliation/deletion occurs only after a fully successful pass. |
| Guest access leaks credentials | Only aggregate public data is exposed; tokens and internal snapshots remain server-side. |

## Initial file inventory

- `.gitmodules`, `vendor/gh-widgets/`: pinned upstream integration.
- `backend/`: focused FastAPI application, ingest, schema, auth, caching, and two dashboard endpoints.
- `public/`, `src/`: Claudit-derived application shell plus the generalized stacked/cumulative time-series component.
- `tests/fixtures/`: small GitHub GraphQL and normalized-snapshot fixtures.
- `tests/`: parser/source, ingest, API, auth, geometry, and frontend contract coverage.
- `examples/ghpulse.service`, `examples/ghpulse-resync.service`: deployment examples.
- `README.md`, `AGENTS.md`: scope, commands, invariants, and operational rules.

## Approval

The user approved the recommended architecture and granted standing approval for subsequent choices to be made according to the most correct design rather than the easiest implementation. This specification records those choices and is the implementation authority.
