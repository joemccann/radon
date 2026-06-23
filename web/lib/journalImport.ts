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
  right?: string;
  ib_exec_id?: string;
}

export interface TradeEntry {
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
  ib_exec_id?: string;
  notes?: string;
  [key: string]: unknown;
}

export interface SyncResult {
  imported: number;
  skipped: number;
  trades: TradeEntry[];
}

function fingerprint(ticker: string, date: string, action: string, qty: number): string {
  return `${ticker}|${date}|${action}|${Math.abs(qty)}`;
}

function execIdParts(id: string | undefined): string[] {
  if (!id) return [];
  const parts = id.split("+").filter(Boolean);
  return parts.length > 0 ? [id, ...parts] : [id];
}

function resolveStructure(secType: string, action: string, strike?: number, expiry?: string, right?: string): string {
  const typeLabel = secType === "STK" ? "Stock" : secType === "OPT" ? "Option" : secType === "BAG" ? "Spread" : secType;
  const side = action.includes("BUY")
    ? "Long"
    : action === "SELL_TO_OPEN"
      ? "Short"
      : action.includes("SELL") || action === "CLOSED"
        ? "Closed"
        : action;

  if ((secType === "OPT" || secType === "BAG") && strike && right) {
    const rightLabel = right === "C" ? "Call" : right === "P" ? "Put" : right;
    const expiryLabel = expiry
      ? expiry.replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3")
      : "";
    return `${side} ${rightLabel} $${strike}${expiryLabel ? ` ${expiryLabel}` : ""}`;
  }

  return `${side} ${typeLabel} (${secType})`;
}

function calcTotalCost(secType: string, qty: number, price: number, commission: number): number {
  const multiplier = secType === "OPT" || secType === "BAG" ? 100 : 1;
  return Math.abs(qty) * price * multiplier + commission;
}

export function syncNewTrades(
  existingTrades: TradeEntry[],
  newTrades: ReconciliationTrade[],
): SyncResult {
  const existingExecIds = new Set<string>();
  const existingFp = new Set<string>();
  for (const trade of existingTrades) {
    for (const part of execIdParts(trade.ib_exec_id)) {
      existingExecIds.add(part);
    }
    const qty = trade.contracts ?? trade.shares ?? 0;
    existingFp.add(fingerprint(trade.ticker, trade.date, trade.action ?? trade.decision, qty));
  }

  const maxId = existingTrades.length > 0
    ? Math.max(...existingTrades.map((trade) => trade.id))
    : 0;

  let nextId = maxId + 1;
  let imported = 0;
  let skipped = 0;
  const importedTrades: TradeEntry[] = [];

  for (const newTrade of newTrades) {
    const execIds = execIdParts(newTrade.ib_exec_id);
    const matchedById = execIds.some((id) => existingExecIds.has(id));
    const fp = fingerprint(newTrade.symbol, newTrade.date, newTrade.action, newTrade.net_quantity);

    if (matchedById || (execIds.length === 0 && existingFp.has(fp))) {
      skipped += 1;
      continue;
    }

    const isOption = newTrade.sec_type === "OPT" || newTrade.sec_type === "BAG";
    const entry: TradeEntry = {
      id: nextId,
      date: newTrade.date,
      ticker: newTrade.symbol,
      structure: resolveStructure(newTrade.sec_type, newTrade.action, newTrade.strike, newTrade.expiry, newTrade.right),
      decision: "IB_AUTO_IMPORT",
      action: newTrade.action,
      fill_price: newTrade.avg_price,
      total_cost: calcTotalCost(newTrade.sec_type, newTrade.net_quantity, newTrade.avg_price, newTrade.commission),
      ...(isOption
        ? { contracts: Math.abs(newTrade.net_quantity) }
        : { shares: Math.abs(newTrade.net_quantity) }),
      commission: newTrade.commission,
      ...(newTrade.ib_exec_id ? { ib_exec_id: newTrade.ib_exec_id } : {}),
      ...(newTrade.realized_pnl !== 0 ? { realized_pnl: newTrade.realized_pnl } : {}),
      notes: `Auto-imported from IB reconciliation on ${new Date().toISOString().split("T")[0]}`,
    };

    nextId += 1;
    importedTrades.push(entry);
    for (const part of execIds) existingExecIds.add(part);
    existingFp.add(fp);
    imported += 1;
  }

  return { imported, skipped, trades: importedTrades };
}
