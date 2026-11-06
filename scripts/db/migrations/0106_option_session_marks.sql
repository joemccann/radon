-- Last session print and book per held option, so a sync or relay restart has a
-- mark that is not the previous close. Replaces data/option_session_mark_cache.json.
CREATE TABLE IF NOT EXISTS option_session_marks (
  mark_key   TEXT PRIMARY KEY,
  last       REAL,
  bid        REAL,
  ask        REAL,
  mid        REAL,
  checked_on TEXT
);
INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(106,datetime('now'));
