/**
 * Journal Auto-Sync — imports IB reconciliation new_trades into trade_log.json
 *
 * Reads reconciliation.json, converts new_trades to TradeEntry format,
 * appends to trade_log.json with decision: "IB_AUTO_IMPORT".
 * Duplicate detection via (ticker + date + action + quantity) fingerprint.
 */

import { readFile, writeFile } from "fs/promises";
import { join } from "path";

/* ─── Types ──────────────────────────────────────────── */

export interface ReconciliationTrade {
  symbol: string;
  date: string;
  action: string;
  net_quantity: number;
  avg_price: number;
  commission: number;
  realized_pnl: number;
  sec_type: string;
  strike?: number;
  expiry?: string;
  right?: string; // "C" or "P"
}

interface ReconciliationData {
  timestamp: string;
  new_trades: ReconciliationTrade[];
  positions_missing_locally: unknown[];
  positions_closed: unknown[];
  needs_attention: boolean;
}

interface TradeEntry {
  id: number;
  date: string;
  ticker: string;
  structure: string;
  decision: string;
  action: string;
  fill_price: number;
  total_cost: number;
  contracts?: number;
  shares?: number;
  realized_pnl?: number;
  commission?: number;
  notes?: string;
  [key: string]: unknown;
}

interface TradeLogData {
  trades: TradeEntry[];
}

/* ─── Helpers ────────────────────────────────────────── */

/** Fingerprint for duplicate detection: ticker|date|action|abs(quantity) */
function fingerprint(ticker: string, date: string, action: string, qty: number): string {
  return `${ticker}|${date}|${action}|${Math.abs(qty)}`;
}

/** Map sec_type + action + optional contract details to a human-readable structure string */
function resolveStructure(secType: string, action: string, strike?: number, expiry?: string, right?: string): string {
  const typeLabel = secType === "STK" ? "Stock" : secType === "OPT" ? "Option" : secType === "BAG" ? "Spread" : secType;
  const side = action.includes("BUY") ? "Long" : action.includes("SELL") || action === "CLOSED" ? "Closed" : action;

  // Include contract details for options when available
  if ((secType === "OPT" || secType === "BAG") && strike && right) {
    const rightLabel = right === "C" ? "Call" : right === "P" ? "Put" : right;
    const expiryLabel = expiry
      ? expiry.replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3")
      : "";
    return `${side} ${rightLabel} $${strike}${expiryLabel ? ` ${expiryLabel}` : ""}`;
  }

  return `${side} ${typeLabel} (${secType})`;
}

/** Calculate total cost: for options multiply by 100 (contract multiplier) */
function calcTotalCost(secType: string, qty: number, price: number, commission: number): number {
  const multiplier = secType === "OPT" || secType === "BAG" ? 100 : 1;
  return Math.abs(qty) * price * multiplier + commission;
}

/* ─── Core Sync Logic (pure, testable) ───────────────── */

export interface SyncResult {
  imported: number;
  skipped: number;
  trades: TradeEntry[];
}

/**
 * Convert reconciliation new_trades into TradeEntry objects,
 * skipping any that already exist in the trade log.
 */
export function syncNewTrades(
  existingTrades: TradeEntry[],
  newTrades: ReconciliationTrade[]
): SyncResult {
  // Build fingerprint set from existing trades
  const existingFp = new Set<string>();
  for (const t of existingTrades) {
    const qty = t.contracts ?? t.shares ?? 0;
    existingFp.add(fingerprint(t.ticker, t.date, t.action ?? t.decision, qty));
  }

  const maxId = existingTrades.length > 0
    ? Math.max(...existingTrades.map((t) => t.id))
    : 0;

  let nextId = maxId + 1;
  let imported = 0;
  let skipped = 0;
  const importedTrades: TradeEntry[] = [];

  for (const nt of newTrades) {
    const fp = fingerprint(nt.symbol, nt.date, nt.action, nt.net_quantity);
    if (existingFp.has(fp)) {
      skipped++;
      continue;
    }

    const isOption = nt.sec_type === "OPT" || nt.sec_type === "BAG";
    const entry: TradeEntry = {
      id: nextId++,
      date: nt.date,
      ticker: nt.symbol,
      structure: resolveStructure(nt.sec_type, nt.action, nt.strike, nt.expiry, nt.right),
      decision: "IB_AUTO_IMPORT",
      action: nt.action,
      fill_price: nt.avg_price,
      total_cost: calcTotalCost(nt.sec_type, nt.net_quantity, nt.avg_price, nt.commission),
      ...(isOption
        ? { contracts: Math.abs(nt.net_quantity) }
        : { shares: Math.abs(nt.net_quantity) }),
      commission: nt.commission,
      ...(nt.realized_pnl !== 0 ? { realized_pnl: nt.realized_pnl } : {}),
      notes: `Auto-imported from IB reconciliation on ${new Date().toISOString().split("T")[0]}`,
    };

    importedTrades.push(entry);
    existingFp.add(fp); // prevent dupes within same batch
    imported++;
  }

  return { imported, skipped, trades: importedTrades };
}

/* ─── File I/O ───────────────────────────────────────── */

const DATA_DIR = join(process.cwd(), "..", "data");

export async function loadReconciliation(): Promise<ReconciliationData> {
  const raw = await readFile(join(DATA_DIR, "reconciliation.json"), "utf-8");
  return JSON.parse(raw);
}

export async function loadTradeLog(): Promise<TradeLogData> {
  const raw = await readFile(join(DATA_DIR, "trade_log.json"), "utf-8");
  return JSON.parse(raw);
}

export async function saveTradeLog(data: TradeLogData): Promise<void> {
  await writeFile(
    join(DATA_DIR, "trade_log.json"),
    JSON.stringify(data, null, 2),
    "utf-8"
  );
}

/**
 * Full sync: read reconciliation → import new trades → write trade_log
 */
export async function runJournalSync(): Promise<SyncResult> {
  const recon = await loadReconciliation();
  const tradeLog = await loadTradeLog();

  if (!recon.new_trades || recon.new_trades.length === 0) {
    return { imported: 0, skipped: 0, trades: [] };
  }

  const result = syncNewTrades(tradeLog.trades, recon.new_trades);

  if (result.imported > 0) {
    tradeLog.trades.push(...result.trades);
    await saveTradeLog(tradeLog);
  }

  return result;
}
