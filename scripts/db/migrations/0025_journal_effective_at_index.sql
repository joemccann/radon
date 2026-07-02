-- 0025_journal_effective_at_index.sql — index the journal's effective-time order.
--
-- read_journal_trades / read_journal_entry_date_maps order the entire journal
-- by COALESCE(filled_at, written_at); idx_journal_filled (filled_at alone)
-- cannot serve that expression, so every read was a full scan + temp-B-tree
-- sort whose cost grows with trade history. This expression index matches the
-- readers' ORDER BY textually (readers.JOURNAL_EFFECTIVE_ORDER_SQL — keep the
-- two in sync) so SQLite walks the index instead of sorting.

CREATE INDEX IF NOT EXISTS idx_journal_effective_at
  ON journal (COALESCE(filled_at, written_at), trade_id);
