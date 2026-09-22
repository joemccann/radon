-- SLM tagger shadow rows (newsfeed text-tagger ladder rung, HR-7).
-- Provenance for C.2 rule 6: never train a future version on slm-produced tags.
CREATE TABLE IF NOT EXISTS slm_tagger_shadow (
  post_id        TEXT NOT NULL,
  observed_at    TEXT NOT NULL,
  model_version  TEXT NOT NULL,
  mode           TEXT NOT NULL,
  tags_slm       TEXT,
  tags_slm_raw   TEXT,
  tags_ladder    TEXT,
  ladder_provider TEXT,
  ladder_sampled INTEGER NOT NULL DEFAULT 0,
  slm_status     TEXT NOT NULL,
  slm_latency_ms INTEGER,
  ladder_latency_ms INTEGER,
  exact3         INTEGER,
  jaccard        REAL,
  PRIMARY KEY (post_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_slm_shadow_observed ON slm_tagger_shadow(observed_at DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (83, datetime('now'));
