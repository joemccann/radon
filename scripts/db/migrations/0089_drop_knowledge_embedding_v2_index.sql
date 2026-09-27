-- 0089_drop_knowledge_embedding_v2_index.sql
--
-- Supersedes the DiskANN index created in 0087. embedding_v2 retrieval is
-- an exact cosine scan (ORDER BY vector_distance_cos). The index is about
-- 5 GB, row inserts take 4.5-33s, and building it cannot finish inside
-- Turso's one-hour statement limit. 0087 itself is unchanged.
--
-- radon-migrate: manual
--
-- DROP INDEX took about 14 minutes on an 11,718-row Turso branch copy.
-- radon-api applies migrations with migrate.py --boot under `timeout 30`
-- and a 20s boot deadline. Running this DROP there kills the statement
-- and refuses to boot. migrate.py also commits ordinary files once, at
-- the end; this DROP must commit on its own (Turso reaps a transaction
-- that is still running). The runner therefore skips this file unless
-- RADON_MIGRATE_MANUAL=1, and then commits each statement before the
-- version row.
--
-- One-off, after the exact-scan code is deployed:
--   RADON_MIGRATE_MANUAL=1 python3.13 scripts/db/migrate.py
-- Confirm the index is gone, then rerun backfill_v2. See
-- docs/knowledge-embeddings.md.

DROP INDEX IF EXISTS idx_knowledge_embedding_v2;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (89, datetime('now'));
