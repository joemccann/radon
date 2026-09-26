-- 0087_knowledge_embedding_v2.sql — optional 2048-d knowledge vectors.
--
-- nvidia/nemotron-3-embed-1b returns 2048 floats and rejects every other
-- dimensions value. Queries default to this index (RADON_KB_EMBED_BACKEND
-- defaults to nvidia; 2026-09-25). The 384-d column stays the automatic
-- fallback, and ingest dual-writes it, until embedding_v2 has no NULLs.
--
-- ORDERING IS LOAD-BEARING. Turso does not backfill libsql_vector_idx for
-- rows written before the index existed (local libsql does, which is why a
-- unit test cannot reproduce the miss). Create the index here, while every
-- embedding_v2 value is still NULL, and only then run
-- scripts/knowledge/backfill_v2.py. This file writes no knowledge rows.

ALTER TABLE knowledge ADD COLUMN embedding_v2 F32_BLOB(2048);

CREATE INDEX IF NOT EXISTS idx_knowledge_embedding_v2
  ON knowledge(libsql_vector_idx(embedding_v2));

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (87, datetime('now'));
