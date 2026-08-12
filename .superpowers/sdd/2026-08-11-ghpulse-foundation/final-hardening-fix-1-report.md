# Final hardening fix 1 report

**Status:** DONE_WITH_CONCERNS

**Commit:** `7551e16f9763a794386e22ae3aef927dbeb22556`

**Scope:** Exactly the three requested Important findings. No push was
performed. The independent reviewer owns the broad gate run currently in
progress.

## Implemented

### Uncached durable sync status

The cached dashboard aggregate no longer computes or stores operational sync
status. Normal `/api/dashboard` route responses overlay the current durable
`sync_state` attempt marker through a separate uncached read. This means a
primed successful aggregate remains byte-for-byte unchanged in its timeline
arrays after a failed attempt, while a new non-`fresh` client sees the latest
failure status and timestamp. Repeated unchanged successes continue to report
their current attempt timestamp.

Public failure status is bounded to `SYNC_FAILED` / `sync failed`; the durable
audit row retains the raw error for operators.

### Public error redaction

Guest/public surfaces no longer expose raw exception strings:

- health uses `SYNC_FAILED` / `sync failed` and
  `DATABASE_UNAVAILABLE` / `database unavailable`;
- `last_ingest` and `last_attempt` use the same bounded code/message;
- `ingest_failed` SSE payloads contain status, code, bounded message, and
  timestamp only;
- the browser no longer appends server `detail` text to request errors.

Sentinel-secret tests verify raw acquisition, database, health, and SSE errors
remain absent from public payloads while remaining in the durable audit row.

### Truthful unbounded shutdown

The service example removes Uvicorn's finite graceful-shutdown timeout and sets
`TimeoutStopSec=infinity`. README/comments now document that authored snapshot
acquisition follows every pagination cursor, with per-request timeouts but no
finite total bound; workers are never terminated under a database transaction.

## TDD evidence

The first regression probe failed in the expected ways: six focused tests
observed cached `success` after a durable failure, raw sentinel errors in
health, raw sentinel database errors, raw ingest failure details, and the old
finite service timeout. After the implementation, the focused run first
reported 61 passed plus one stale connection-count contract. That contract was
updated to assert the intentional separate aggregate/status reads; the final
focused command passed completely:

```text
GHPULSE_TEST_DATABASE_URL=postgresql:///ghpulse_hardening_luna \
DATABASE_URL_VIZ=postgresql:///ghpulse_hardening_luna \
DATABASE_URL_AUTH=postgresql:///auth_hardening_luna \
ADMIN_TOKEN=hardening COOKIE_SECURE=0 \
python3 -W error::DeprecationWarning -m pytest \
  tests/test_api.py tests/test_app.py tests/test_app_contract.py \
  tests/test_health.py tests/test_ingest.py -q -ra --tb=short
```

Result: **62 passed in 22.15s**.

## Files changed

- `README.md`
- `backend/api_common.py`
- `backend/api_dashboard.py`
- `backend/app.py`
- `backend/ingest.py`
- `examples/ghpulse.service`
- `src/app.jsx`
- `tests/test_api.py`
- `tests/test_app.py`
- `tests/test_app_contract.py`
- `tests/test_health.py`
- `tests/test_ingest.py`

`vendor/gh-widgets` was not modified; its approved pin remains
`54d2ec72a804a4c7968a7682ccd5b4cbdd7dd713` (`54d2ec7`).

## Remaining gate

No broad suite, static-quality suite, or fresh full browser/renderer gate was
rerun by this bounded closeout after commit; an independent reviewer process
was already running those gates. No remote push was performed.
