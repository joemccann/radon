-- The Held review needs enough about a document to judge it when the intake
-- selected nothing: opening text, page and chart counts, where the report
-- date came from, the selector's own rationale and the authenticated PDF
-- link. One JSON column keeps the mirror additive.
ALTER TABLE research_outcomes ADD COLUMN context_json TEXT NOT NULL DEFAULT '{}';
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (82, datetime('now'));
