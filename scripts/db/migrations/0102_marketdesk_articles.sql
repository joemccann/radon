-- MarketDesk research ingest ledger (scripts/research/marketdesk_store.py).
-- One row per marketdesk.ai library article: publish idempotency, failure cap,
-- and the Dropbox upload outbox. post_id is research-<32 hex>; pdf_asset is the
-- private /api/newsfeed/research/files/<sha>.pdf URL.
CREATE TABLE IF NOT EXISTS marketdesk_articles (
  article_id TEXT PRIMARY KEY,
  post_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','published','failed')),
  title TEXT NOT NULL DEFAULT '',
  article_url TEXT NOT NULL,
  published_at TEXT NOT NULL,
  pdf_asset TEXT,
  pdf_sha256 TEXT,
  dropbox_status TEXT CHECK (dropbox_status IS NULL OR dropbox_status IN ('uploaded','skipped_no_creds','failed')),
  dropbox_path TEXT,
  dropbox_id TEXT,
  dropbox_rev TEXT,
  dropbox_content_hash TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  first_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_marketdesk_articles_upload
  ON marketdesk_articles(status, dropbox_status);
-- The shared-folder Dropbox watcher (radon-research.service) is retired; drop its health row.
DELETE FROM service_health WHERE service = 'dropbox-research';
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (102, datetime('now'));
