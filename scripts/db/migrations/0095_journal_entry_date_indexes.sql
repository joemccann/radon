-- 0095_journal_entry_date_indexes.sql — index the entry-date journal reads.
--
-- GET /api/portfolio?include=entry-dates (web/app/api/portfolio/route.ts,
-- loadTradeLogDates / loadContractOpenDates) filters and groups the journal on
-- json_extract(payload, ...). No index matched those expressions, so both
-- reads were a full journal scan, and the per-ticker MAX(date) added a temp
-- B-tree for its GROUP BY.
--
-- Expression indexes, not generated columns: they need no ALTER TABLE on the
-- journal, leave its column list unchanged, and replay safely with
-- IF NOT EXISTS (same pattern as 0025). The expressions and partial WHERE
-- clauses match the route's SQL textually; keep the two in sync or the
-- planner falls back to a scan
-- (web/tests/portfolio-entry-dates-query-plan.test.ts).

CREATE INDEX IF NOT EXISTS idx_journal_ticker
  ON journal (json_extract(payload, '$.ticker'))
  WHERE json_extract(payload, '$.ticker') IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_journal_option_contract
  ON journal (json_extract(payload, '$.right'), json_extract(payload, '$.strike'))
  WHERE json_extract(payload, '$.right') IS NOT NULL
    AND json_extract(payload, '$.strike') IS NOT NULL;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (95, datetime('now'));
