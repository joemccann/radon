-- 0037_position_return_capital.sql
-- Immutable execution episodes plus isolated IB account-margin observations.
-- Generic Return % accepts only v2 observations that cover the current event.

CREATE TABLE IF NOT EXISTS position_execution_facts (
  account_id      TEXT NOT NULL,
  exec_id         TEXT NOT NULL,
  revision        INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
  payload_sha256  TEXT NOT NULL CHECK (length(payload_sha256) = 64),
  perm_id         INTEGER,
  order_ref       TEXT,
  con_id          INTEGER NOT NULL,
  side            TEXT NOT NULL CHECK (side IN ('BOT', 'BUY', 'SLD', 'SELL')),
  quantity        REAL NOT NULL CHECK (quantity > 0),
  price           REAL NOT NULL CHECK (price >= 0),
  multiplier      REAL NOT NULL CHECK (multiplier > 0),
  currency        TEXT NOT NULL,
  filled_at       TEXT NOT NULL,
  payload         TEXT NOT NULL CHECK (json_valid(payload)),
  ingested_at     TEXT NOT NULL,
  PRIMARY KEY (account_id, exec_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_position_execution_facts_time
  ON position_execution_facts (account_id, filled_at, exec_id);
CREATE INDEX IF NOT EXISTS idx_position_execution_facts_order
  ON position_execution_facts (account_id, perm_id, order_ref, filled_at);

CREATE TABLE IF NOT EXISTS position_instances (
  instance_id      TEXT PRIMARY KEY,
  account_id       TEXT NOT NULL,
  strategy_key     TEXT NOT NULL,
  episode          INTEGER NOT NULL CHECK (episode > 0),
  opened_at        TEXT NOT NULL,
  opening_exec_id  TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  UNIQUE (account_id, strategy_key, episode)
);

CREATE INDEX IF NOT EXISTS idx_position_instances_lookup
  ON position_instances (account_id, strategy_key, episode DESC);

CREATE TABLE IF NOT EXISTS position_instance_events (
  event_id          TEXT PRIMARY KEY,
  instance_id       TEXT NOT NULL REFERENCES position_instances(instance_id),
  kind              TEXT NOT NULL CHECK (kind IN ('OPEN', 'ADD', 'REDUCE', 'CLOSE', 'REVERSAL')),
  transaction_key   TEXT NOT NULL,
  effective_at      TEXT NOT NULL,
  before_legs       TEXT NOT NULL CHECK (json_valid(before_legs)),
  after_legs        TEXT NOT NULL CHECK (json_valid(after_legs)),
  exec_ids          TEXT NOT NULL CHECK (json_valid(exec_ids)),
  source_digest     TEXT NOT NULL CHECK (length(source_digest) = 64),
  recorded_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_position_instance_events_latest
  ON position_instance_events (instance_id, effective_at DESC, event_id DESC);

CREATE TABLE IF NOT EXISTS position_event_executions (
  event_id          TEXT NOT NULL REFERENCES position_instance_events(event_id),
  account_id        TEXT NOT NULL,
  exec_id           TEXT NOT NULL,
  revision          INTEGER NOT NULL DEFAULT 1,
  con_id            INTEGER NOT NULL,
  perm_id           INTEGER,
  order_ref         TEXT,
  signed_quantity   REAL NOT NULL CHECK (signed_quantity <> 0),
  price             REAL NOT NULL CHECK (price >= 0),
  multiplier        REAL NOT NULL CHECK (multiplier > 0),
  currency          TEXT NOT NULL,
  filled_at         TEXT NOT NULL,
  PRIMARY KEY (event_id, account_id, exec_id, revision, con_id)
);

CREATE INDEX IF NOT EXISTS idx_position_event_executions_exec
  ON position_event_executions (account_id, exec_id, revision);

CREATE TABLE IF NOT EXISTS account_margin_samples (
  sample_id          TEXT PRIMARY KEY,
  account_id         TEXT NOT NULL,
  observed_from      TEXT NOT NULL,
  observed_through   TEXT NOT NULL,
  initial_margin     REAL NOT NULL CHECK (initial_margin >= 0),
  maintenance_margin REAL CHECK (maintenance_margin >= 0),
  currency           TEXT NOT NULL,
  positions          TEXT NOT NULL CHECK (json_valid(positions)),
  positions_sha256   TEXT NOT NULL CHECK (length(positions_sha256) = 64),
  source             TEXT NOT NULL CHECK (source = 'ib-account-values'),
  idempotency_key    TEXT NOT NULL UNIQUE,
  recorded_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_account_margin_samples_time
  ON account_margin_samples (account_id, observed_from, observed_through);

CREATE TABLE IF NOT EXISTS position_capital_observations (
  observation_id   TEXT PRIMARY KEY,
  instance_id      TEXT NOT NULL REFERENCES position_instances(instance_id),
  through_event_id TEXT NOT NULL REFERENCES position_instance_events(event_id),
  status           TEXT NOT NULL CHECK (status IN ('VALID', 'VOID')),
  method           TEXT NOT NULL CHECK (method IN ('DEBIT_CASH', 'DEFINED_MAX_LOSS', 'OBSERVED_INIT_MARGIN_DELTA')),
  quality          TEXT NOT NULL CHECK (quality IN ('exact', 'observed')),
  amount           REAL,
  delta_amount     REAL,
  before_sample_id TEXT REFERENCES account_margin_samples(sample_id),
  after_sample_id  TEXT REFERENCES account_margin_samples(sample_id),
  currency         TEXT NOT NULL,
  source           TEXT NOT NULL,
  evidence         TEXT NOT NULL CHECK (json_valid(evidence)),
  idempotency_key  TEXT NOT NULL UNIQUE,
  recorded_at      TEXT NOT NULL,
  CHECK ((status = 'VALID' AND amount IS NOT NULL AND amount >= 0)
      OR (status = 'VOID' AND amount IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_position_capital_observations_latest
  ON position_capital_observations (instance_id, recorded_at DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
VALUES (37, datetime('now'));
