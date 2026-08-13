/**
 * Bounded Turso reads for the assistant journal tools.
 *
 * All reads go through the `dbExecute` chokepoint (never bare
 * `getDb().execute`) and are paginated/bounded per the Turso Hrana I/O
 * rules. The WHERE expression must stay textually
 * `COALESCE(filled_at, written_at)` so `idx_journal_effective_at`
 * (migration 0025) serves it.
 */

import type { ResultSet } from "@libsql/client";
import { dbExecute } from "@/lib/dbExecute";
import { shiftEtDay } from "@/lib/journal/rangePnl";
import type { JournalTradePayload } from "@/lib/blotter/fromJournal";
import type { RealizedJournalRow } from "@/lib/journal/realizedPnl";

const JOURNAL_DB_TIMEOUT_MS = 5_000;
const JOURNAL_DB_LABEL = "assistant-journal";
export const WINDOW_ROW_LIMIT = 1_000;
export const PRIOR_ROW_LIMIT = 500;

/**
 * How far back the widened fetch looks for opens that predate the queried
 * window. Positions opened earlier than this mis-base — a stated
 * limitation, same bound philosophy as the journal readers.
 */
export const PRIOR_OPEN_LOOKBACK_DAYS = 180;

function safeParsePayload(raw: unknown): JournalTradePayload | null {
  if (typeof raw !== "string") return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as JournalTradePayload;
    }
    return null;
  } catch {
    return null;
  }
}

function rowsFromResult(rs: ResultSet): RealizedJournalRow[] {
  const out: RealizedJournalRow[] = [];
  for (const row of rs.rows) {
    const payload = safeParsePayload(row.payload);
    if (!payload) continue;
    out.push({
      trade_id: row.trade_id != null ? String(row.trade_id) : undefined,
      payload,
      filled_at: typeof row.filled_at === "string" ? row.filled_at : null,
    });
  }
  return out;
}

/**
 * Journal rows whose effective time falls in [fromEt-1d, toEt+2d) — the
 * ±1-day slop keeps UTC ISO timestamps that map to edge ET days from being
 * lost; precise ET-day filtering happens in the pure layer via toEtDay().
 */
export async function fetchJournalRowsInRange(
  fromEt: string,
  toEt: string,
): Promise<RealizedJournalRow[]> {
  const rs = await dbExecute(
    {
      sql: `SELECT trade_id, payload, filled_at FROM journal
            WHERE COALESCE(filled_at, written_at) >= ? AND COALESCE(filled_at, written_at) < ?
            ORDER BY COALESCE(filled_at, written_at) ASC LIMIT ${WINDOW_ROW_LIMIT}`,
      args: [shiftEtDay(fromEt, -1), shiftEtDay(toEt, 2)],
    },
    { timeoutMs: JOURNAL_DB_TIMEOUT_MS, label: JOURNAL_DB_LABEL },
  );
  return rowsFromResult(rs);
}

/**
 * Widened fetch for opens that predate the window: rows for `tickers` in
 * [beforeEt-180d, beforeEt).
 */
export async function fetchPriorRowsForTickers(
  tickers: string[],
  beforeEt: string,
): Promise<{ rows: RealizedJournalRow[]; truncated: boolean }> {
  if (tickers.length === 0) return { rows: [], truncated: false };
  const upper = tickers.map((t) => t.toUpperCase());
  const placeholders = upper.map(() => "?").join(",");
  const rs = await dbExecute(
    {
      sql: `SELECT trade_id, payload, filled_at FROM journal
            WHERE COALESCE(filled_at, written_at) >= ? AND COALESCE(filled_at, written_at) < ?
              AND UPPER(COALESCE(json_extract(payload,'$.ticker'), json_extract(payload,'$.symbol'))) IN (${placeholders})
            ORDER BY COALESCE(filled_at, written_at) DESC LIMIT ${PRIOR_ROW_LIMIT + 1}`,
      args: [shiftEtDay(beforeEt, -PRIOR_OPEN_LOOKBACK_DAYS), beforeEt, ...upper],
    },
    { timeoutMs: JOURNAL_DB_TIMEOUT_MS, label: JOURNAL_DB_LABEL },
  );
  const rows = rowsFromResult(rs);
  return {
    rows: rows.slice(0, PRIOR_ROW_LIMIT).reverse(),
    truncated: rows.length > PRIOR_ROW_LIMIT,
  };
}
