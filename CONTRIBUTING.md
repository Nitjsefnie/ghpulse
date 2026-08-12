# Contributing to ghpulse

Contributions should preserve the product's small, current-state surface. Read
[`AGENTS.md`](AGENTS.md) and [`README.md`](README.md) before changing code.

## Scope

ghpulse reports public GitHub activity for one configured account in external
repositories. The initial dashboard has exactly two panels: issues and pull
requests. It is not a raw issue browser, an OAuth account manager, a private
repository viewer, or a historical transition ledger. `gh-widgets` remains a
standalone SVG renderer and is consumed only through its pinned public data
boundary.

## Development

```bash
git submodule update --init --recursive
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example .env
```

Use a disposable PostgreSQL database for integration tests and set
`GHPULSE_TEST_DATABASE_URL`. Apply `backend/schema.sql` before local smoke
tests. Do not use production credentials in fixtures or test output.

New behavior follows test-driven development: write a focused failing test,
observe the expected failure, implement the smallest behavior, then run the
focused and full suites. Keep source snapshots complete and immutable at the
adapter boundary; persistence must reconcile unseen rows only after successful
validation and commit.

## Verification

Run the commands that apply to the change and include their actual results in
the PR:

```bash
python3 -m pytest tests/ -q
python3 -m pyright
python3 -m pylint backend
python3 -m pycodestyle backend tests
python3 -m ruff check backend tests
python3 -m unittest discover -v -s vendor/gh-widgets
python3 /root/.agent-bundle/scripts/ctrlchar_audit.py
```

The frontend is intentionally no-build: React and Babel load before the chart
and app JSX modules. Exercise the executable Bun/Node contracts whenever
frontend or static serving changes. Do not turn a missing CI dependency into a
pytest skip.

## Security and operations

The GitHub token is server-only. Admin ingest requires `X-Admin-Token`, checked
with constant-time comparison, and a same-origin request. Do not add secrets to
HTML, JavaScript, logs, fixtures, screenshots, or API payloads. The service
example runs startup and hourly complete ingest in-process; do not add a second
timer or an incremental/resync mode.

## Commits and pull requests

Keep commits focused and include the exact verification commands and output in
the PR body. Agent-authored commits disclose the producing model with the
required `Co-Authored-By` trailer. Do not claim a test or smoke run that was not
actually executed.
