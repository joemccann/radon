-- 0019_workflow_graphs.sql — F14 visual flow-pipeline node-graph composer.
--
-- One row per saved workflow graph, user-scoped (mirrors the alert_rules /
-- bookmarks pattern). The graph itself is stored as a JSON payload
-- ({nodes, edges}); the denormalised name + updated_at support cheap listing.
-- The last execution summary is cached so the canvas can show "last run" status
-- without replaying the graph.

CREATE TABLE IF NOT EXISTS workflow_graphs (
  id            TEXT    PRIMARY KEY,        -- uuid
  user_id       TEXT    NOT NULL,
  name          TEXT    NOT NULL,
  graph         TEXT    NOT NULL,           -- JSON {nodes:[...], edges:[...]}
  created_at    TEXT    NOT NULL,           -- ISO-8601 UTC
  updated_at    TEXT    NOT NULL,           -- ISO-8601 UTC
  last_run_at   TEXT,                       -- ISO-8601 UTC, null until first run
  last_run_ok   INTEGER,                    -- 1 ok / 0 blocked, null until first run
  last_run_summary TEXT                     -- JSON GraphReport summary, nullable
);

CREATE INDEX IF NOT EXISTS idx_workflow_graphs_user
  ON workflow_graphs(user_id, updated_at DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (19, datetime('now'));
