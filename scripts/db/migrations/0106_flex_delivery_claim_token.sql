-- 0106_flex_delivery_claim_token.sql — the Flex delivery lease names its owner.
--
-- `release_flex_delivery` (DELETE) and `mark_flex_delivery_applied` (UPDATE)
-- matched on content_sha256 alone. A run that outlived
-- FLEX_CLAIM_STALE_AFTER_S lost its lease to a second run, then deleted or
-- flipped that run's live claim. Each claim now writes a fresh claim_token
-- and both statements compare it, so only the claimant can end its lease.
--
-- Rows claimed before this migration carry NULL: an applied row never
-- changes again, and a stale in_progress row is re-taken with a token.
--
-- Idempotent through migrate.py's `duplicate column name` marker (R-153).
-- Formatting is load-bearing: keep each terminating semicolon at end-of-line.

ALTER TABLE flex_deliveries ADD COLUMN claim_token TEXT;

INSERT OR IGNORE INTO schema_migrations (version, applied_at)
  VALUES (106, datetime('now'));
