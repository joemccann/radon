-- 0063_flex_delivery_claim_status.sql — R-436: the Flex delivery claim carries
-- its lifecycle.
--
-- `claim_flex_delivery` is taken BEFORE the writers and released by one
-- best-effort DELETE against the same Turso the writer just failed on, so the
-- outage that half-writes `cash_flows` also keeps the claim, and the next run
-- of the same bytes is `ok`/`duplicate` over a half-applied statement.
--
-- status:     'in_progress' from the claim until every writer has committed,
--             'applied' after. A stale in_progress row (claimed_at older than
--             one run period) is claimable again; 'applied' never is.
-- claimed_at: when the current lease was taken; the staleness clock.
--
-- Rows claimed before this migration default to 'applied' so the R-326
-- duplicate gate holds for everything already ingested.
--
-- Each ALTER is idempotent through migrate.py's `duplicate column name`
-- marker (R-153), so a replay after a kill between the two statements and the
-- version row continues instead of aborting.
--
-- WARNING — formatting is load-bearing: migrate.py:_split_statements and
-- migrate.ts:splitStatements split on /;\s*$/m (semicolon at end-of-line =
-- statement boundary). Keep every statement's terminating semicolon at
-- end-of-line and never put an interior semicolon at end-of-line.

ALTER TABLE flex_deliveries ADD COLUMN status TEXT NOT NULL DEFAULT 'applied';

ALTER TABLE flex_deliveries ADD COLUMN claimed_at TEXT;
