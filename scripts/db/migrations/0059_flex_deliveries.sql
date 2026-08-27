-- 0059_flex_deliveries.sql — ingested Flex XML fingerprints
--
-- content_sha256 is the PK so the same file is never applied twice.
-- classified_as is activity (1442520) or trades (1422766).

CREATE TABLE IF NOT EXISTS flex_deliveries (
  content_sha256 TEXT PRIMARY KEY,
  classified_as  TEXT NOT NULL,
  period_from    TEXT,
  period_to      TEXT,
  ingested_at    TEXT NOT NULL,
  source_path    TEXT
);

CREATE INDEX IF NOT EXISTS idx_flex_deliveries_ingested
  ON flex_deliveries (ingested_at DESC);
