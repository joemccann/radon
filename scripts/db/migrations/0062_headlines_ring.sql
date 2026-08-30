-- 0062_headlines_ring.sql — durable copy of the MKTNews hub's headline ring
--
-- The hub (scripts/mktnews/hub.js) keeps the last RING_SIZE prints in memory
-- and serves them as the handshake snapshot; this table lets a restart
-- rehydrate that ring instead of serving an empty tape. Each ingest upserts
-- one row and prunes everything past the newest RING_SIZE, so the table
-- never grows beyond the ring. Not an archive.

CREATE TABLE IF NOT EXISTS headlines_ring (
  id          TEXT PRIMARY KEY,
  time        TEXT,
  important   INTEGER NOT NULL DEFAULT 0,
  content     TEXT NOT NULL,
  impact      TEXT NOT NULL DEFAULT '[]',
  received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_headlines_ring_received
  ON headlines_ring (received_at DESC);
