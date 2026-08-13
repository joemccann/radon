/**
 * Per-share stock basis from the latest portfolio snapshot.
 *
 * IB's per-share avg_cost lives on the snapshot's stock legs even when the
 * shares' opening trades predate the journal corpus (e.g. delivered by an
 * assignment before rehydrate captured such rows). `get_realized_pnl` passes
 * this map to `computeRealizedPnl` as `stockBasisFallback` so an
 * assignment/exercise-coded delivery still realizes share P&L.
 */

import { dbExecute } from "@/lib/dbExecute";

const SNAPSHOT_TIMEOUT_MS = 3_000;

export type StockBasisPoint = { takenAt: string; avgCost: number };
export type StockBasisHistory = Record<string, StockBasisPoint[]>;

function stockLegBasis(position: unknown): { ticker: string; avgCost: number; accountId: string | null } | null {
  if (!position || typeof position !== "object") return null;
  const ticker = String((position as { ticker?: unknown }).ticker ?? "").toUpperCase();
  if (!ticker) return null;
  const legs = (position as { legs?: unknown }).legs;
  if (!Array.isArray(legs)) return null;
  for (const leg of legs) {
    if (!leg || typeof leg !== "object") continue;
    if ((leg as { type?: unknown }).type !== "Stock") continue;
    const avgCost = (leg as { avg_cost?: unknown }).avg_cost;
    if (typeof avgCost === "number" && Number.isFinite(avgCost) && avgCost !== 0) {
      const rawAccount = (position as { account_id?: unknown; account?: unknown }).account_id
        ?? (position as { account?: unknown }).account;
      const accountId = typeof rawAccount === "string" && rawAccount.trim() ? rawAccount.trim() : null;
      return { ticker, avgCost: Math.abs(avgCost), accountId };
    }
  }
  return null;
}

export async function fetchPortfolioStockBasis(): Promise<StockBasisHistory> {
  const result = await dbExecute(
    {
      sql: "SELECT taken_at, payload FROM portfolio_snapshots ORDER BY taken_at ASC LIMIT ?",
      args: [5000],
    },
    { label: "portfolio stock basis", timeoutMs: SNAPSHOT_TIMEOUT_MS },
  );
  const basis: StockBasisHistory = {};
  const accountsByTicker = new Map<string, Set<string>>();
  for (const rawRow of result.rows) {
    const row = rawRow as unknown as { taken_at?: unknown; payload?: unknown };
    if (typeof row.payload !== "string" || typeof row.taken_at !== "string") continue;
    let snapshot: unknown;
    try {
      snapshot = JSON.parse(row.payload);
    } catch {
      continue;
    }
    const positions = (snapshot as { positions?: unknown })?.positions;
    if (!Array.isArray(positions)) continue;
    for (const position of positions) {
      const stock = stockLegBasis(position);
      if (!stock) continue;
      const accountKey = stock.accountId ?? "";
      const key = `${accountKey}|${stock.ticker}`;
      (basis[key] ??= []).push({ takenAt: row.taken_at, avgCost: stock.avgCost });
      const accounts = accountsByTicker.get(stock.ticker) ?? new Set<string>();
      accounts.add(accountKey);
      accountsByTicker.set(stock.ticker, accounts);
    }
  }
  for (const [ticker, accounts] of accountsByTicker) {
    if (accounts.size === 1) {
      const [account] = accounts;
      basis[ticker] = basis[`${account}|${ticker}`];
    }
  }
  return basis;
}
