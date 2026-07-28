-- 0030_assistant_turns.sql — per-turn assistant telemetry.
--
-- The 2026-07-24 max-rounds incident was undiagnosable: toolEvents/rounds/usage
-- existed in memory and were discarded; the only durable evidence was one Caddy
-- access-log line. One row per /api/assistant turn, written fire-and-forget by
-- web/lib/assistant/telemetry.ts — the write must never fail or delay the request.

CREATE TABLE IF NOT EXISTS assistant_turns (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,             -- ISO8601 UTC
  user_msg   TEXT,                      -- last user message, truncated to 200 chars
  rounds     INTEGER NOT NULL,
  tool_calls TEXT NOT NULL,             -- JSON: [{name, ok, repeated?, error?}]
  usage      TEXT,                      -- JSON: {inputTokens, outputTokens}
  outcome    TEXT NOT NULL              -- answered | proposal | cap_forced_final | cap_fallback | error
);

CREATE INDEX IF NOT EXISTS idx_assistant_turns_ts ON assistant_turns (ts);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (30, datetime('now'));
