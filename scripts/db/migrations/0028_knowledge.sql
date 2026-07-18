-- 0028_knowledge.sql — Radon knowledge base (Phase 0): one shared table for
-- every connector's distilled exhaust (journal, eval reports, newsfeed, docs,
-- incidents), per tasks/knowledge-base-plan.md.
--
-- One row per document chunk, keyed by (source, doc_key, chunk_ix).
-- content is the raw text consumers quote; summary is the distilled retrieval
-- key; embedding is bge-small-en-v1.5 (384d), NULL until the embed step runs.
-- content_hash = sha256 over every connector-supplied field (source, scope,
-- doc_key, chunk_ix, title, summary, content, metadata), length-prefixed —
-- the incremental-ingest idempotency key (skip unchanged, update changed).
--
-- ORDERING IS LOAD-BEARING: Turso does NOT backfill libsql_vector_idx on
-- CREATE INDEX over existing rows (verified in the Phase 0 spike — the ANN
-- index silently returns an empty set for rows inserted before it existed),
-- so the vector index MUST be created here, before any ingest ever runs.
--
-- knowledge_fts is a plain FTS5 mirror (stores its own copy, rowid =
-- knowledge.id) maintained by scripts/knowledge/store.py. Not triggers:
-- the spike only proved plain AFTER INSERT triggers remotely, and the
-- external-content update/delete trigger bodies need multi-statement BEGIN
-- blocks that migrate.py's `;$` splitter cannot carry.

CREATE TABLE IF NOT EXISTS knowledge (
  id               INTEGER PRIMARY KEY,
  source           TEXT NOT NULL,       -- connector: 'journal' | 'evals' | 'newsfeed' | 'docs' | 'incidents'
  scope            TEXT NOT NULL,       -- 'trading' | 'research' | 'ops'
  doc_key          TEXT NOT NULL,       -- stable per-document key within source
  chunk_ix         INTEGER NOT NULL DEFAULT 0,
  title            TEXT,
  summary          TEXT,                -- distilled searchable form (retrieval key only)
  content          TEXT NOT NULL,       -- raw text; consumers cite/quote this
  metadata         TEXT,                -- JSON (tickers, tags, source-specific fields)
  embedding        F32_BLOB(384),       -- bge-small-en-v1.5; NULL until embedded
  content_hash     TEXT NOT NULL,       -- sha256(source|doc_key|chunk_ix|content|summary)
  created_at       TEXT NOT NULL,       -- UTC ISO, first ingest
  last_activity_at TEXT NOT NULL,       -- UTC ISO, drives per-source recency decay
  UNIQUE (source, doc_key, chunk_ix)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_source_last_activity
  ON knowledge(source, last_activity_at DESC);

CREATE INDEX IF NOT EXISTS idx_knowledge_embedding
  ON knowledge(libsql_vector_idx(embedding));

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(title, summary, content);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (28, datetime('now'));
