-- ghpulse stores one current row per GitHub node.  Dashboard outcome events
-- are derived from the mutable state below; there is intentionally no event
-- or relationship table.

CREATE TABLE IF NOT EXISTS repositories (
  node_id          TEXT PRIMARY KEY,
  name_with_owner  TEXT NOT NULL,
  owner_login      TEXT NOT NULL,
  url              TEXT NOT NULL,
  is_private       BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_private IS FALSE),
  is_external      BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_external IS TRUE),
  first_seen_at    TIMESTAMPTZ NOT NULL,
  last_seen_at     TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
  node_id          TEXT PRIMARY KEY,
  repository_id    TEXT NOT NULL REFERENCES repositories(node_id) ON DELETE CASCADE,
  number           INTEGER NOT NULL CHECK (number > 0),
  url              TEXT NOT NULL,
  is_private       BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_private IS FALSE),
  created_at       TIMESTAMPTZ NOT NULL,
  updated_at       TIMESTAMPTZ NOT NULL,
  closed_at        TIMESTAMPTZ,
  state            TEXT NOT NULL CHECK (state IN ('OPEN', 'CLOSED')),
  state_reason     TEXT,
  CONSTRAINT issues_state_reason_shape_check CHECK (
    state_reason IS NULL OR
    (length(state_reason) BETWEEN 1 AND 64 AND btrim(state_reason) <> '')
  ),
  CONSTRAINT issues_state_reason_state_check CHECK (
    (state = 'OPEN' AND closed_at IS NULL
     AND (state_reason IS NULL OR state_reason NOT IN ('COMPLETED', 'NOT_PLANNED')))
    OR (state = 'CLOSED' AND closed_at IS NOT NULL
        AND state_reason IS NOT NULL AND state_reason <> 'REOPENED')
  )
);

CREATE INDEX IF NOT EXISTS issues_repository_idx ON issues (repository_id);
CREATE INDEX IF NOT EXISTS issues_created_at_idx ON issues (created_at);
CREATE INDEX IF NOT EXISTS issues_updated_at_idx ON issues (updated_at);
CREATE INDEX IF NOT EXISTS issues_closed_at_idx ON issues (closed_at);

CREATE TABLE IF NOT EXISTS pull_requests (
  node_id          TEXT PRIMARY KEY,
  repository_id    TEXT NOT NULL REFERENCES repositories(node_id) ON DELETE CASCADE,
  number           INTEGER NOT NULL CHECK (number > 0),
  url              TEXT NOT NULL,
  is_private       BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_private IS FALSE),
  created_at       TIMESTAMPTZ NOT NULL,
  updated_at       TIMESTAMPTZ NOT NULL,
  closed_at        TIMESTAMPTZ,
  merged_at        TIMESTAMPTZ,
  state            TEXT NOT NULL CHECK (state IN ('OPEN', 'CLOSED', 'MERGED')),
  merged           BOOLEAN NOT NULL DEFAULT FALSE,
  CHECK (state = 'OPEN' AND merged IS FALSE AND closed_at IS NULL AND merged_at IS NULL
         OR state = 'CLOSED' AND merged IS FALSE AND closed_at IS NOT NULL
         OR state = 'MERGED' AND merged IS TRUE AND closed_at IS NOT NULL
                         AND merged_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS pull_requests_repository_idx ON pull_requests (repository_id);
CREATE INDEX IF NOT EXISTS pull_requests_created_at_idx ON pull_requests (created_at);
CREATE INDEX IF NOT EXISTS pull_requests_updated_at_idx ON pull_requests (updated_at);
CREATE INDEX IF NOT EXISTS pull_requests_closed_at_idx ON pull_requests (closed_at);
CREATE INDEX IF NOT EXISTS pull_requests_merged_at_idx ON pull_requests (merged_at);

CREATE TABLE IF NOT EXISTS ingest_runs (
  id                         BIGSERIAL PRIMARY KEY,
  trigger                    TEXT NOT NULL,
  started_at                 TIMESTAMPTZ NOT NULL,
  finished_at                TIMESTAMPTZ,
  committed_at               TIMESTAMPTZ,
  source_snapshot_at         TIMESTAMPTZ,
  repositories_fetched       INTEGER NOT NULL DEFAULT 0,
  issues_fetched             INTEGER NOT NULL DEFAULT 0,
  pull_requests_fetched      INTEGER NOT NULL DEFAULT 0,
  repositories_upserted      INTEGER NOT NULL DEFAULT 0,
  issues_upserted            INTEGER NOT NULL DEFAULT 0,
  pull_requests_upserted     INTEGER NOT NULL DEFAULT 0,
  repositories_deleted       INTEGER NOT NULL DEFAULT 0,
  issues_deleted             INTEGER NOT NULL DEFAULT 0,
  pull_requests_deleted      INTEGER NOT NULL DEFAULT 0,
  data_changed               BOOLEAN NOT NULL DEFAULT FALSE,
  error                      TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
  id                       SMALLINT PRIMARY KEY CHECK (id = 1),
  last_committed_at        TIMESTAMPTZ,
  last_source_snapshot_at  TIMESTAMPTZ,
  last_attempt_at          TIMESTAMPTZ,
  last_attempt_status      TEXT,
  last_attempt_error       TEXT,
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Existing deployments may still have the pre-current-state columns.  The
-- current ingest never reads or writes those obsolete flags; these additive
-- migrations let an already-created database acquire the authoritative
-- commit/source timestamps and remove the obsolete metadata columns.
ALTER TABLE ingest_runs ADD COLUMN IF NOT EXISTS committed_at TIMESTAMPTZ;
ALTER TABLE ingest_runs ADD COLUMN IF NOT EXISTS source_snapshot_at TIMESTAMPTZ;
ALTER TABLE ingest_runs ADD COLUMN IF NOT EXISTS data_changed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sync_state ADD COLUMN IF NOT EXISTS last_committed_at TIMESTAMPTZ;
ALTER TABLE sync_state ADD COLUMN IF NOT EXISTS last_source_snapshot_at TIMESTAMPTZ;
ALTER TABLE sync_state ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ;
ALTER TABLE sync_state ADD COLUMN IF NOT EXISTS last_attempt_status TEXT;
ALTER TABLE sync_state ADD COLUMN IF NOT EXISTS last_attempt_error TEXT;
ALTER TABLE ingest_runs DROP COLUMN IF EXISTS full_sync;
ALTER TABLE sync_state DROP COLUMN IF EXISTS last_successful_high_water;
ALTER TABLE sync_state DROP CONSTRAINT IF EXISTS sync_state_attempt_status_check;
ALTER TABLE sync_state ADD CONSTRAINT sync_state_attempt_status_check CHECK (
  last_attempt_status IS NULL OR last_attempt_status IN ('success', 'failure')
);

-- Replace the original v1 issue-reason checks with a bounded forward-compatible
-- shape check.  Unknown future reasons remain current state but are ignored by
-- the known COMPLETED/NOT_PLANNED aggregate predicates.
ALTER TABLE issues DROP CONSTRAINT IF EXISTS issues_state_reason_check;
ALTER TABLE issues DROP CONSTRAINT IF EXISTS issues_check;
ALTER TABLE issues DROP CONSTRAINT IF EXISTS issues_state_reason_shape_check;
ALTER TABLE issues DROP CONSTRAINT IF EXISTS issues_state_reason_state_check;
ALTER TABLE issues ADD CONSTRAINT issues_state_reason_shape_check CHECK (
  state_reason IS NULL OR
  (length(state_reason) BETWEEN 1 AND 64 AND btrim(state_reason) <> '')
);
ALTER TABLE issues ADD CONSTRAINT issues_state_reason_state_check CHECK (
  (state = 'OPEN' AND closed_at IS NULL
   AND (state_reason IS NULL OR state_reason NOT IN ('COMPLETED', 'NOT_PLANNED')))
  OR (state = 'CLOSED' AND closed_at IS NOT NULL
      AND state_reason IS NOT NULL AND state_reason <> 'REOPENED')
);

INSERT INTO sync_state (id)
VALUES (1)
ON CONFLICT (id) DO NOTHING;
