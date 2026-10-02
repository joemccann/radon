-- Review time and TTL-expiry time as their own columns so the 24h held
-- sweep can stop overwriting updated_at (the decision/review stamp).
-- held_at is unknown for rows that already aged out before this column.
ALTER TABLE research_outcomes ADD COLUMN held_at TEXT;
ALTER TABLE research_outcomes ADD COLUMN expired_at TEXT;

UPDATE research_outcomes
SET expired_at = updated_at
WHERE expired_at IS NULL
  AND outcome = 'dropped'
  AND instr(COALESCE(reason_codes, ''), 'HELD_EXPIRED') > 0;

UPDATE research_outcomes
SET held_at = updated_at
WHERE held_at IS NULL
  AND outcome = 'held';

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (91, datetime('now'));
