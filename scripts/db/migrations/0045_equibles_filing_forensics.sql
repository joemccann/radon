-- 0045_equibles_filing_forensics.sql — pre-trade filing-forensics dossier.
--
-- One row per ticker holding the CURRENT dossier: the SEC filing surface that
-- silently kills convex long structures (Form 144 forward-looking insider
-- supply, unused ATM shelf capacity, standing buyback authorization, 8-K Item
-- 5.02 executive departures, going-concern doubt). The refresh rewrites the
-- row in place; history is not kept because the dossier is a pre-trade check
-- against the LATEST filing state, not a time series.
--
-- payload is the full dossier JSON (checks with per-check verdicts, cited
-- filings, and per-source status). flag_count and data_complete are lifted out
-- as columns so the flagged-listing query never has to parse JSON:
-- data_complete = 0 means at least one source failed, so an absent flag on
-- this row means "could not verify", NOT "all clear".

CREATE TABLE IF NOT EXISTS equibles_filing_forensics (
  ticker        TEXT PRIMARY KEY,
  as_of         TEXT NOT NULL,     -- ISO8601 UTC of the refresh that built it
  flag_count    INTEGER NOT NULL,  -- checks whose verdict is 'flagged'
  data_complete INTEGER NOT NULL,  -- 1 = every source answered, 0 = partial
  payload       TEXT NOT NULL,     -- full dossier JSON
  recorded_at   TEXT NOT NULL
);

-- Serves the route's flagged listing: WHERE flag_count > 0
-- ORDER BY flag_count DESC, as_of DESC. Per-ticker reads use the PK.
CREATE INDEX IF NOT EXISTS idx_equibles_filing_forensics_flagged
  ON equibles_filing_forensics(flag_count DESC, as_of DESC);

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (45, datetime('now'));
