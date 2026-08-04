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

function stockLegBasis(position: unknown): { ticker: string; avgCost: number } | null {
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
      return { ticker, avgCost: Math.abs(avgCost) };
    }
  }
  return null;
}

export async function fetchPortfolioStockBasis(): Promise<Record<string, number>> {
  const result = await dbExecute(
    {
      sql: "SELECT payload FROM portfolio_snapshots ORDER BY taken_at DESC LIMIT 1",
      args: [],
    },
    { label: "portfolio stock basis", timeoutMs: SNAPSHOT_TIMEOUT_MS },
  );
  const row = result.rows[0] as unknown as { payload?: unknown } | undefined;
  if (!row || typeof row.payload !== "string") return {};

  let snapshot: unknown;
  try {
    snapshot = JSON.parse(row.payload);
  } catch {
    return {};
  }
  const positions = (snapshot as { positions?: unknown })?.positions;
  if (!Array.isArray(positions)) return {};

  const basis: Record<string, number> = {};
  for (const position of positions) {
    const stock = stockLegBasis(position);
    if (stock) basis[stock.ticker] = stock.avgCost;
  }
  return basis;
}
