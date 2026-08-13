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
  ib_exec_id_corrected?: string;
  notes?: string;
  [key: string]: unknown;
}

export interface SyncResult {
  imported: number;
  skipped: number;
  trades: TradeEntry[];
  needs_rehydration?: boolean;
}

function fingerprint(ticker: string, date: string, action: string, qty: number): string {
  return `${ticker}|${date}|${action}|${Math.abs(qty)}`;
}

type ExecIdentity = { raw: string; root: string; correction: number };

function execIdentities(id: string | undefined): ExecIdentity[] {
  if (!id) return [];
  return id.split("+").filter(Boolean).map((raw) => {
    const match = raw.match(/^(.*)\.(\d+)$/);
    return match
      ? { raw, root: match[1], correction: Number(match[2]) }
      : { raw, root: raw, correction: 0 };
  });
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

function calcTotalCost(secType: string, action: string, qty: number, price: number, commission: number): number {
  const multiplier = secType === "OPT" || secType === "BAG" ? 100 : 1;
  const gross = Math.abs(qty) * price * multiplier;
  return action.includes("SELL") || action === "CLOSED" ? gross - commission : gross + commission;
}

export function syncNewTrades(
  existingTrades: TradeEntry[],
  newTrades: ReconciliationTrade[],
): SyncResult {
  const existingExecIds = new Set<string>();
  const existingByRoot = new Map<string, { trade: TradeEntry; correction: number }>();
  const existingFp = new Set<string>();
  for (const trade of existingTrades) {
    for (const identity of execIdentities(trade.ib_exec_id_corrected ?? trade.ib_exec_id)) {
      existingExecIds.add(identity.root);
      const known = existingByRoot.get(identity.root);
      if (!known || identity.correction > known.correction) {
        existingByRoot.set(identity.root, { trade, correction: identity.correction });
      }
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
  let needsRehydration = false;
  const importedTrades: TradeEntry[] = [];

  for (const newTrade of newTrades) {
    const identities = execIdentities(newTrade.ib_exec_id);
    const matchedIdentities = identities.filter((identity) => existingExecIds.has(identity.root));
    const matchedById = matchedIdentities.length > 0;
    const fp = fingerprint(newTrade.symbol, newTrade.date, newTrade.action, newTrade.net_quantity);

    if (newTrade.action === "CLOSED" && newTrade.net_quantity === 0) {
      skipped += 1;
      needsRehydration = true;
      continue;
    }

    if (matchedIdentities.length > 0 && matchedIdentities.length < identities.length) {
      skipped += 1;
      needsRehydration = true;
      continue;
    }

    const correctionTarget = identities.length === 1
      ? existingByRoot.get(identities[0].root)
      : undefined;
    const isNewerCorrection = Boolean(
      correctionTarget && identities[0].correction > correctionTarget.correction,
    );

    if ((matchedById && !isNewerCorrection) || (identities.length === 0 && existingFp.has(fp))) {
      skipped += 1;
      continue;
    }

    const isOption = newTrade.sec_type === "OPT" || newTrade.sec_type === "BAG";
    const entry: TradeEntry = {
      id: isNewerCorrection ? correctionTarget!.trade.id : nextId,
      date: newTrade.date,
      ticker: newTrade.symbol,
      structure: resolveStructure(newTrade.sec_type, newTrade.action, newTrade.strike, newTrade.expiry, newTrade.right),
      decision: "IB_AUTO_IMPORT",
      action: newTrade.action,
      fill_price: newTrade.avg_price,
      total_cost: calcTotalCost(newTrade.sec_type, newTrade.action, newTrade.net_quantity, newTrade.avg_price, newTrade.commission),
      ...(isOption
        ? { contracts: Math.abs(newTrade.net_quantity) }
        : { shares: Math.abs(newTrade.net_quantity) }),
      commission: newTrade.commission,
      ...(newTrade.ib_exec_id ? {
        ib_exec_id: isNewerCorrection ? correctionTarget!.trade.ib_exec_id : newTrade.ib_exec_id,
        ...(isNewerCorrection ? { ib_exec_id_corrected: newTrade.ib_exec_id } : {}),
      } : {}),
      ...(newTrade.realized_pnl !== 0 ? { realized_pnl: newTrade.realized_pnl } : {}),
      notes: `Auto-imported from IB reconciliation on ${new Date().toISOString().split("T")[0]}`,
    };

    if (!isNewerCorrection) nextId += 1;
    importedTrades.push(entry);
    for (const identity of identities) {
      existingExecIds.add(identity.root);
      existingByRoot.set(identity.root, { trade: entry, correction: identity.correction });
    }
    existingFp.add(fp);
    imported += 1;
  }

  return { imported, skipped, trades: importedTrades, ...(needsRehydration ? { needs_rehydration: true } : {}) };
}
