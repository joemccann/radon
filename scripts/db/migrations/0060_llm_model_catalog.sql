-- LLM model catalog: the frontier chat model the chat picker offers for each
-- provider. One row per provider (provider is the PK) because the picker shows
-- exactly one entry per provider - re-keying on (provider, model_id) would let
-- retired ids accumulate and the picker would offer models nobody can call.
-- radon-model-catalog.timer rewrites a provider's row only when a live model
-- list answered, so a failed or rate-limited poll leaves the last good row in
-- place. A provider whose API key is absent from the deployment simply has no
-- row; the route omits it from the picker rather than showing it disabled.
CREATE TABLE IF NOT EXISTS llm_model_catalog (
    provider TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    refreshed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_model_catalog_refreshed_at ON llm_model_catalog (refreshed_at DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (60, datetime('now'));
