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
python3 -m pytest tests/ -q --cov=backend
python3 -m pyright
python3 -m pylint backend
python3 -m pycodestyle backend tests
python3 -m ruff check backend tests
python3 /root/.agent-bundle/scripts/ctrlchar_audit.py
```

`pip install -r requirements-dev.txt -r requirements-test.txt` gets the
pinned toolchain. Run it against an environment holding the pinned runtime
deps too (`pip install -r backend/requirements.txt`): pyright resolves
third-party types from the installed packages, so a stale local psycopg
makes it disagree with CI.

Run the Bun JSX/Babel contracts and the pinned gh-widgets unittest suite before
claiming a change is complete. Do not weaken tests by adding skips when a CI
dependency is missing. The CI workflow provisions PostgreSQL and Bun.

## CI

**Six workflows, not one.** `tests.yml` is the big one and still does the
most: pytest (now with coverage), pyright, pylint, pycodestyle, ruff, the
Bun transpile contracts, the Chromium smoke, the final-integration
boundary, and the pinned gh-widgets suite. The other five:

| Workflow | Question it answers | Trigger |
| --- | --- | --- |
| `codeql.yml` | Is there a security defect in the Python or JS? Results go to the Security tab, never the build. | push/PR + weekly cron. The cron is NOT redundant: a query published today would otherwise only ever run against files touched after it shipped. |
| `audit.yml` | Are the frozen pins still free of advisories? Resolves the full transitive tree, which is the point — nothing here pins `starlette`, it arrives under fastapi. | push/PR + **daily** cron. The cron is the important half: this answer changes with no commit to hang it on. |
| `actionlint.yml` | Is the workflow YAML itself well-formed and safe? A broken workflow does not go red, it silently stops running. | changes under `.github/`. |
| `speed.yml` | Did the tests that exist in both this commit and the last release get >30% slower? | push/PR. Runs BOTH builds on the same runner, interleaved, min-of-rounds. Excludes browser tests, whose duration is dominated by Chromium startup. Skips green while no release exists. |
| `release.yml` | — | push to `main` touching `VERSION`. Waits for every other check on that SHA, then tags `v<VERSION>`. |

There is deliberately **no separate smoke workflow** here, unlike the
sibling dashboards. `tests/test_final_integration.py` and the Chromium
smoke already exercise the real boundary, and CI runs both explicitly.

**Coverage is a ratchet at 87%**, checked by a step of its own inside
`tests.yml` so "tests failed" and "coverage dropped" stay distinguishable.
Raise the floor as coverage climbs; never lower it to turn a build green.

**Release = edit `VERSION`.** One bare semver line at the repo root, no
leading `v`. `backend/version.py` reads it and `/health` reports it.
Nothing bumps it automatically: deciding patch-vs-minor is a judgement
about what changed.

**Actions are hash-pinned**, with the version in a trailing comment. Do
not "tidy" one back to `@v4`: a tag is a moving pointer, and these jobs
hold a repository token. Dependabot keeps the hashes current — including
the `vendor/gh-widgets` submodule pin, which has the same failure mode.

**Tool pins live in `requirements-dev.txt` / `requirements-test.txt`**,
not inline in a workflow's install step. Inline pins are invisible to
Dependabot and cannot be installed locally with one command, so they rot
in place.

**`.gitignore` is deny-by-default**: `*` first, then each shipped path
named back. A new file of an unlisted type is invisible to git and will
NOT appear in `git status` — `git check-ignore -v <path>` names the rule
hiding it. Never "fix" it by loosening the leading `*`.

For deployment, use `examples/ghpulse.service`. Startup and hourly ingestion
are in-process APScheduler jobs. Do not add a second systemd timer or invent
incremental, weekly, resync, or export modes.
