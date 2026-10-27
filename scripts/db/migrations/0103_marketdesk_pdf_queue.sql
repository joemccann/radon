-- Independent, durable PDF archive. Article publication never waits on this queue.
CREATE TABLE IF NOT EXISTS marketdesk_pdf_queue (
  article_id TEXT PRIMARY KEY,
  article_url TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  published_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','downloading','downloaded')),
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL,
  lease_until TEXT,
  lease_token TEXT,
  pdf_asset TEXT,
  pdf_sha256 TEXT,
  attached_post_id TEXT,
  last_error TEXT,
  first_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_marketdesk_pdf_due
  ON marketdesk_pdf_queue(status,next_attempt_at,published_at,article_id);
CREATE TABLE IF NOT EXISTS marketdesk_pdf_throttle (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  next_attempt_at TEXT NOT NULL
);
INSERT OR IGNORE INTO marketdesk_pdf_throttle VALUES(1,'1970-01-01T00:00:00+00:00');
INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(103,datetime('now'));
