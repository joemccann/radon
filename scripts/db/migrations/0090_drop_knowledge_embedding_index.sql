-- 0090_drop_knowledge_embedding_index.sql
--
-- Drops the 384-d DiskANN index from 0028, as 0089 did for embedding_v2.
-- On a radon fork (2026-09-28) one row's embedding UPDATE took 10-26s while
-- every other statement took ~0.03s. Knowledge ingest held Turso's single
-- writer for that long per chunk, so every other writer's 4s request timed
-- out (fleet-wide stall 2026-09-27 00:11-00:18 UTC). The 384-d leg is now an
-- exact cosine scan (scripts/knowledge/retrieve.py). 0028 is unchanged.
--
-- radon-migrate: manual
--
-- Long DDL: skipped on boot; commits on its own under the manual runner.
-- One-off, after the exact-scan code is deployed:
--   RADON_MIGRATE_MANUAL=1 python3.13 scripts/db/migrate.py
-- Confirm the index is gone before re-enabling radon-knowledge.timer.

DROP INDEX IF EXISTS idx_knowledge_embedding;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (90, datetime('now'));
