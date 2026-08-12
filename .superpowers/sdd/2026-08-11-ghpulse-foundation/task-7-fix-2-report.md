# Task 7 fix round 2 report

## Status

DONE

## Change

- Configured the real browser-smoke Uvicorn server with `ws="none"` because
  ghpulse exposes SSE and has no WebSocket endpoint. This prevents Uvicorn from
  importing its unused deprecated WebSocket stack.
- Made the CI browser invocation strict with
  `python -W error::DeprecationWarning -m pytest ...`.

## Verification

- `GHPULSE_BROWSER_SMOKE=1 python3 -W error::DeprecationWarning -m pytest tests/test_browser_smoke.py -q -m browser` — 1 passed, no warnings.
- `python3 -W error::DeprecationWarning -m pytest tests/test_app.py tests/test_health.py -q` — 12 passed.
- `python3 -m pyright` — 0 errors, 0 warnings, 0 informations.
- `python3 -m pylint backend --score=no` — passed.
- `python3 -m pycodestyle backend tests` — passed.
- `python3 -m ruff check backend tests` — passed.
- `git diff --check` — clean.

## Files changed

- `tests/test_browser_smoke.py`
- `.github/workflows/tests.yml`

No lifecycle or unrelated application code was changed.
