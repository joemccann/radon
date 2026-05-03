/**
 * Blotter from Journal — derive the /orders (blotter) view from
 * the canonical Turso `journal` table.
 *
 * Why this exists:
 *   The legacy blotter pipeline (Flex Query 1422766 → blotter_service.py
 *   → data/blotter.json) is broken IB-side ("1001 — Statement could
 *   not be generated") and frozen at 2026-03-26. The `journal` table
 *   already holds every execution we know about — the same data, just
 *   shaped per-row instead of per-trade-grouping. This function
 *   projects journal rows into the BlotterPayload shape that
 *   WorkspaceSections.HistoricalTradesSection consumes.
 *
 * Mapping (journal row → BlotterTrade):
 *   Each journal row is one execution-grouped action (BUY_OPTION,
 *   SELL_OPTION, BUY, SELL, SELL_TO_OPEN, CLOSED). One row → one
 *   BlotterTrade with one BlotterExecution. Multi-fill executions are
 *   already collapsed by journal_rehydrate.py (composite ib_exec_id).
 *
 * Status:
 *   - realized_pnl present  → closed (round-trip materialised)
 *   - action contains BUY   → open long
 *   - SELL_TO_OPEN          → open short
 *   - SELL_OPTION / SELL without realized_pnl → fall back to closed
 *     (legacy rows from before realized_pnl was tracked)
 */

export interface JournalTradePayload {
  id?: number;
  date?: string;
  filled_at?: string;
  ticker?: string;
  symbol?: string;
  structure?: string;
  decision?: string;
  action?: string;
  fill_price?: number;
  total_cost?: number;
  contracts?: number;
  shares?: number;
  commission?: number;
  realized_pnl?: number;
  // Persisted by journal_rehydrate.py:_compute_pnl_summary so the deriver
  // doesn't have to reconstruct lot-matched P&L from row-level totals.
  cost_basis?: number;
  proceeds?: number;
  realized_quantity?: number;
  total_round_trip_quantity?: number;
  ib_exec_id?: string;
  strike?: number;
  right?: string;
  expiry?: string;
  notes?: string;
  [key: string]: unknown;
}

export interface JournalRow {
  payload: JournalTradePayload;
  filled_at?: string | null;
}

export interface BlotterExecutionShape {
  exec_id: string;
  time: string;
  side: string;
  quantity: number;
  price: number;
  commission: number;
  notional_value: number;
  net_cash_flow: number;
}

export interface BlotterTradeShape {
  symbol: string;
  contract_desc: string;
  sec_type: string;
  is_closed: boolean;
  net_quantity: number;
  total_quantity?: number;
  total_commission: number;
  realized_pnl: number | null;
  realized_quantity?: number;
  realized_cost_basis?: number | null;
  cost_basis: number;
  proceeds: number;
  total_cash_flow: number;
  executions: BlotterExecutionShape[];
}

export interface BlotterPayload {
  as_of: string;
  summary: {
    closed_trades: number;
    open_trades: number;
    total_commissions: number;
    realized_pnl: number;
  };
  closed_trades: BlotterTradeShape[];
  open_trades: BlotterTradeShape[];
}

const OPT_SEC_TYPES = new Set(["OPT", "BAG"]);

function resolveTicker(p: JournalTradePayload): string {
  return (p.ticker || p.symbol || "").toString();
}

function resolveSecType(p: JournalTradePayload): string {
  if (p.structure && /\bSpread\b/.test(p.structure)) return "BAG";
  if (p.strike != null || p.right || p.expiry) return "OPT";
  if (p.contracts != null) return "OPT";
  if (p.shares != null) return "STK";
  // Fall back to OPT/STK based on action label.
  if (p.action && /OPTION/.test(p.action)) return "OPT";
  return "STK";
}

function resolveContractDesc(p: JournalTradePayload, secType: string): string {
  const ticker = resolveTicker(p);
  if (secType === "STK") return `${ticker} (STK)`;
  if (p.structure) return `${ticker} ${p.structure}`;
  return `${ticker} (${secType})`;
}

function resolveSide(action: string): string {
  // IB exec sides: BOT (bought) / SLD (sold).
  if (!action) return "BOT";
  if (action === "BUY" || action === "BUY_OPTION") return "BOT";
  if (action === "SELL" || action === "SELL_OPTION" || action === "SELL_TO_OPEN" || action === "CLOSED") return "SLD";
  return action.includes("BUY") ? "BOT" : "SLD";
}

function resolveQuantity(p: JournalTradePayload): number {
  if (typeof p.contracts === "number") return Math.abs(p.contracts);
  if (typeof p.shares === "number") return Math.abs(p.shares);
  return 0;
}

function resolveExecTime(p: JournalTradePayload, fallback: string | null | undefined): string {
  return p.filled_at || fallback || p.date || "";
}

function isOpeningAction(action: string): boolean {
  return action === "BUY" || action === "BUY_OPTION" || action === "SELL_TO_OPEN";
}

function isClosingAction(action: string): boolean {
  return action === "SELL" || action === "SELL_OPTION" || action === "CLOSED";
}

function rowIsClosed(p: JournalTradePayload): boolean {
  if (typeof p.realized_pnl === "number" && p.realized_pnl !== 0) return true;
  if (isClosingAction(p.action || "")) return true;
  return false;
}

function maxFilledAt(rows: JournalRow[]): string {
  let max = "";
  for (const r of rows) {
    const candidate = r.filled_at || r.payload.filled_at || r.payload.date || "";
    if (candidate && candidate > max) max = candidate;
  }
  return max;
}

function rowToBlotterTrade(row: JournalRow): BlotterTradeShape {
  const p = row.payload;
  const action = p.action || "";
  const secType = resolveSecType(p);
  const ticker = resolveTicker(p);
  const quantity = resolveQuantity(p);
  const price = typeof p.fill_price === "number" ? p.fill_price : 0;
  const commission = typeof p.commission === "number" ? p.commission : 0;
  const multiplier = OPT_SEC_TYPES.has(secType) ? 100 : 1;
  const notionalValue = quantity * price * multiplier;
  const side = resolveSide(action);
  // Net cash flow: BOT spends cash (negative), SLD receives cash (positive).
  // Commission always reduces cash on hand.
  const netCashFlow = side === "BOT"
    ? -(notionalValue + commission)
    : notionalValue - commission;

  const isClosed = rowIsClosed(p);
  const realizedPnl = typeof p.realized_pnl === "number" ? p.realized_pnl : null;

  // Prefer the lot-matched fields persisted by journal_rehydrate.py
  // (cost_basis / proceeds). Fall back to the row-level total_cost
  // approximation only for legacy rows imported before _compute_pnl_summary
  // existed (live ib_reconcile.py rows pre-fix, manual journal entries).
  let costBasis: number;
  let proceeds: number;
  if (typeof p.cost_basis === "number" || typeof p.proceeds === "number") {
    costBasis = typeof p.cost_basis === "number" ? p.cost_basis : 0;
    proceeds = typeof p.proceeds === "number" ? p.proceeds : 0;
  } else if (isClosed) {
    const totalCost = typeof p.total_cost === "number" ? p.total_cost : notionalValue;
    if (realizedPnl != null) {
      proceeds = totalCost;
      costBasis = Math.max(0, totalCost - realizedPnl);
    } else {
      proceeds = totalCost;
      costBasis = totalCost;
    }
  } else {
    costBasis = typeof p.total_cost === "number" ? p.total_cost : notionalValue;
    proceeds = 0;
  }

  const execId = (p.ib_exec_id || `${ticker}-${p.id ?? row.filled_at ?? ""}`).toString();
  const execution: BlotterExecutionShape = {
    exec_id: execId,
    time: resolveExecTime(p, row.filled_at),
    side,
    quantity,
    price,
    commission,
    notional_value: notionalValue,
    net_cash_flow: netCashFlow,
  };

  const isOpen = !isClosed && isOpeningAction(action);
  const totalQuantity =
    typeof p.total_round_trip_quantity === "number"
      ? p.total_round_trip_quantity
      : quantity;
  const realizedQuantity =
    typeof p.realized_quantity === "number" ? p.realized_quantity : undefined;
  const trade: BlotterTradeShape = {
    symbol: ticker,
    contract_desc: resolveContractDesc(p, secType),
    sec_type: secType,
    is_closed: isClosed,
    net_quantity: isOpen ? (action === "SELL_TO_OPEN" ? -quantity : quantity) : 0,
    total_quantity: totalQuantity,
    total_commission: commission,
    realized_pnl: realizedPnl,
    cost_basis: costBasis,
    proceeds,
    total_cash_flow: isClosed ? (realizedPnl ?? 0) : netCashFlow,
    executions: [execution],
  };
  if (realizedQuantity !== undefined) trade.realized_quantity = realizedQuantity;
  return trade;
}

/* ─── Lot-matching backfill for legacy rows ────────────────────────────── */

interface SyntheticExec {
  side: "BUY" | "SELL";
  qty: number;
  notional: number;
  commission: number;
  when: string;
  rowIdx: number;
}

interface LotPnl {
  realized_pnl: number;
  realized_qty: number;
  cost_basis: number;
  proceeds: number;
}

function contractGroupKey(p: JournalTradePayload): string | null {
  const ticker = resolveTicker(p);
  if (!ticker) return null;
  if (p.contracts != null || p.strike != null || p.right || p.expiry) {
    return `${ticker}|OPT|${p.strike ?? ""}|${p.expiry ?? ""}|${p.right ?? ""}`;
  }
  if (p.shares != null) return `${ticker}|STK`;
  return null;
}

function rowSide(action: string): "BUY" | "SELL" | "FLAT" | null {
  const a = (action || "").toUpperCase();
  if (a === "BUY" || a === "BUY_OPTION") return "BUY";
  if (a === "SELL" || a === "SELL_TO_OPEN" || a === "SELL_OPTION") return "SELL";
  if (a === "CLOSED") return "FLAT";
  return null;
}

function rowToSyntheticExecs(
  p: JournalTradePayload,
  rowIdx: number,
  when: string,
): SyntheticExec[] {
  const side = rowSide(p.action || "");
  if (side === null) return [];
  const qty = resolveQuantity(p);
  if (qty <= 0) return [];
  const price = typeof p.fill_price === "number" ? p.fill_price : 0;
  const mult = p.contracts != null ? 100 : 1;
  const notional = qty * price * mult;
  const commission = typeof p.commission === "number" ? p.commission : 0;

  if (side === "FLAT") {
    // A net-flat CLOSED row collapses buy + sell volume into one
    // record at a single volume-weighted average price — that's the
    // shape the legacy journal_rehydrate emitted before the
    // _compute_pnl_summary fix. There's no way to recover the original
    // buy and sell prices from this aggregate, so the synth result for
    // a FLAT-only contract group is intrinsically lossy. We treat the
    // row as a buy + sell at the same price (P&L ≈ 0 at this row alone)
    // — accurate IFF the contract was day-traded at one fill, otherwise
    // the next rehydrate of this contract will repair the numbers.
    const halfCommission = commission / 2;
    return [
      { side: "BUY", qty, notional, commission: halfCommission, when, rowIdx },
      { side: "SELL", qty, notional, commission: commission - halfCommission, when, rowIdx },
    ];
  }
  return [{ side, qty, notional, commission, when, rowIdx }];
}

function inventoryPnl(execs: SyntheticExec[]): LotPnl {
  let positionQty = 0;
  let avgBasis = 0;
  let realizedQty = 0;
  let realizedPnl = 0;
  let costBasis = 0;
  let proceeds = 0;

  const sorted = [...execs].sort((a, b) => (a.when < b.when ? -1 : a.when > b.when ? 1 : 0));
  for (const e of sorted) {
    const isBuy = e.side === "BUY";
    const opening = isBuy ? e.notional + e.commission : e.notional - e.commission;
    if (isBuy) costBasis += e.notional + e.commission;
    else proceeds += e.notional - e.commission;

    const signed = isBuy ? e.qty : -e.qty;
    const sameDirection =
      positionQty === 0 ||
      (positionQty > 0 && signed > 0) ||
      (positionQty < 0 && signed < 0);

    if (sameDirection) {
      const currentBasis = avgBasis * Math.abs(positionQty);
      positionQty += signed;
      avgBasis = positionQty !== 0 ? (currentBasis + opening) / Math.abs(positionQty) : 0;
      continue;
    }

    const closeQty = Math.min(Math.abs(positionQty), e.qty);
    if (closeQty > 0) {
      const basisClosed = avgBasis * closeQty;
      realizedQty += closeQty;
      if (positionQty > 0 && !isBuy) {
        const closeValuePerUnit = (e.notional - e.commission) / e.qty;
        realizedPnl += closeValuePerUnit * closeQty - basisClosed;
      } else if (positionQty < 0 && isBuy) {
        const coverCostPerUnit = (e.notional + e.commission) / e.qty;
        realizedPnl += basisClosed - coverCostPerUnit * closeQty;
      }
      const remaining = Math.abs(positionQty) - closeQty;
      positionQty = remaining > 0 ? (positionQty > 0 ? remaining : -remaining) : 0;
      if (positionQty === 0) avgBasis = 0;
    }

    const residual = e.qty - closeQty;
    if (residual > 0) {
      positionQty = isBuy ? residual : -residual;
      avgBasis = isBuy ? (e.notional + e.commission) / e.qty : (e.notional - e.commission) / e.qty;
    }
  }

  return {
    realized_pnl: realizedPnl,
    realized_qty: realizedQty,
    cost_basis: costBasis,
    proceeds,
  };
}

interface SynthFields {
  realized_pnl: number | null;
  cost_basis: number;
  proceeds: number;
  realized_quantity: number;
  total_round_trip_quantity: number;
  is_closed: boolean;
}

/**
 * Build a per-row P&L lookup for journal rows that lack explicit
 * cost_basis / proceeds / realized_pnl. Rows already carrying those
 * fields are passed through untouched.
 *
 * Strategy: group rows by contract, compute lot-matched group P&L,
 * then apportion it across closing rows by their close volume.
 *
 * Limitation: legacy rehydrated CLOSED rows aggregated buy + sell into
 * one record at a single volume-weighted price, which is information-
 * losing — the synth can recover the contract's net cash flow but not
 * its realized P&L. For those rows the deriver falls back to the
 * row-level heuristic (cost_basis = total_cost, proceeds = total_cost).
 * After journal_rehydrate runs with _compute_pnl_summary, the row
 * carries explicit fields and synth is bypassed entirely.
 */
function buildLotMatchedFields(rows: JournalRow[]): Map<number, SynthFields> {
  const out = new Map<number, SynthFields>();
  const groups = new Map<string, number[]>();
  rows.forEach((row, idx) => {
    if (!row?.payload || typeof row.payload !== "object") return;
    const p = row.payload;
    // Skip rows that already carry any explicit P&L field — those came
    // from journal_rehydrate's lot-matcher (or from ib_reconcile's IB-
    // provided realized_pnl) and are authoritative.
    if (
      typeof p.cost_basis === "number"
      || typeof p.proceeds === "number"
      || typeof p.realized_pnl === "number"
    ) {
      return;
    }
    const key = contractGroupKey(p);
    if (!key) return;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(idx);
  });

  for (const indices of groups.values()) {
    const synthExecs: SyntheticExec[] = [];
    indices.forEach((idx) => {
      const row = rows[idx];
      const when = row.filled_at || row.payload.filled_at || row.payload.date || "";
      synthExecs.push(...rowToSyntheticExecs(row.payload, idx, when));
    });
    if (synthExecs.length === 0) continue;

    const groupPnl = inventoryPnl(synthExecs);
    const totalRoundTripQty = groupPnl.realized_qty;
    const isAnyClosed = totalRoundTripQty > 0;

    // Open-only contracts keep their existing fallback behaviour
    // (rowToBlotterTrade reads total_cost as cost_basis). Synth's
    // value-add is exclusively for closed / partial-close groups
    // where cross-row lot matching is required.
    if (!isAnyClosed) continue;

    // Per-row buckets: which rows contribute opening vs closing volume?
    // For each row we also need to know how much of the group's basis /
    // proceeds it owns (partial-close awareness).
    let allocatedPnl = 0;
    let allocatedBasis = 0;
    let allocatedProceeds = 0;
    let allocatedRealizedQty = 0;

    // Sum row qty as weight; CLOSED rows weight as 2 * qty (buy+sell).
    const weights: Map<number, number> = new Map();
    let totalWeight = 0;
    for (const idx of indices) {
      const p = rows[idx].payload;
      const side = rowSide(p.action || "");
      if (side === null) continue;
      const qty = resolveQuantity(p);
      const w = side === "FLAT" ? qty * 2 : qty;
      weights.set(idx, w);
      totalWeight += w;
    }

    if (totalWeight === 0) continue;

    const orderedIdx = indices.filter((idx) => weights.has(idx));
    orderedIdx.forEach((idx, i) => {
      const w = weights.get(idx)!;
      const isLast = i === orderedIdx.length - 1;
      const ratio = w / totalWeight;
      const p = rows[idx].payload;
      const side = rowSide(p.action || "");
      const qty = resolveQuantity(p);
      const price = typeof p.fill_price === "number" ? p.fill_price : 0;
      const mult = p.contracts != null ? 100 : 1;
      const commission = typeof p.commission === "number" ? p.commission : 0;
      const rowNotional = qty * price * mult;

      let rowCostBasis = 0;
      let rowProceeds = 0;
      if (side === "BUY") {
        rowCostBasis = rowNotional + commission;
      } else if (side === "SELL") {
        rowProceeds = rowNotional - commission;
      } else if (side === "FLAT") {
        // Split CLOSED row commission across both legs.
        rowCostBasis = rowNotional + commission / 2;
        rowProceeds = rowNotional - commission / 2;
      }

      let rowRealizedPnl: number | null = isAnyClosed ? 0 : null;
      let rowRealizedQty = 0;
      let rowIsClosed = false;
      if (isAnyClosed) {
        rowIsClosed = side === "FLAT" || side === "SELL";
        if (rowIsClosed) {
          // Pro-rata share of group P&L for closing rows. Use sell-leg
          // weight (qty for SELL, qty for FLAT — half the total since
          // FLAT contributes both buy and sell).
          const sellWeight = side === "FLAT" ? qty : qty;
          const sellTotalWeight = orderedIdx
            .map((j) => {
              const sj = rowSide(rows[j].payload.action || "");
              const qj = resolveQuantity(rows[j].payload);
              if (sj === "SELL" || sj === "FLAT") return qj;
              return 0;
            })
            .reduce((a, b) => a + b, 0);
          rowRealizedPnl =
            sellTotalWeight > 0
              ? (groupPnl.realized_pnl * sellWeight) / sellTotalWeight
              : 0;
          rowRealizedQty = sellWeight;
        }
      }

      // Apply rounding remainder to the last contributing row so the
      // group totals reconcile exactly. Only when there's any realized
      // P&L to distribute — pure-open contracts keep null.
      if (isAnyClosed) {
        if (isLast) {
          rowRealizedPnl = groupPnl.realized_pnl - allocatedPnl;
        } else {
          allocatedPnl += rowRealizedPnl ?? 0;
        }
      }
      allocatedBasis += rowCostBasis;
      allocatedProceeds += rowProceeds;
      allocatedRealizedQty += rowRealizedQty;

      out.set(idx, {
        realized_pnl: rowRealizedPnl,
        cost_basis: rowCostBasis,
        proceeds: rowProceeds,
        realized_quantity: rowRealizedQty,
        total_round_trip_quantity: isAnyClosed ? totalRoundTripQty : qty,
        is_closed: rowIsClosed,
      });
    });
  }

  return out;
}

/**
 * Project a list of journal rows into the BlotterPayload shape the
 * /orders historical-trades panel expects.
 *
 * Pure function — no I/O, no side effects.
 */
export function journalRowsToBlotter(rows: JournalRow[]): BlotterPayload {
  const closed: BlotterTradeShape[] = [];
  const open: BlotterTradeShape[] = [];
  let totalCommissions = 0;
  let realizedPnl = 0;

  // Pre-pass: synthesize cost_basis / proceeds / realized_pnl for legacy
  // rows that lack them. Rows that already carry the explicit fields are
  // skipped (rowToBlotterTrade reads them directly).
  const synthFields = buildLotMatchedFields(rows);

  rows.forEach((row, idx) => {
    if (!row?.payload || typeof row.payload !== "object") return;
    const synth = synthFields.get(idx);
    if (synth) {
      // Inject synthesized fields into the payload so rowToBlotterTrade
      // reads them as if they were persisted. realized_pnl is left
      // unset (null synth value) when the contract is still fully open.
      const merged: JournalTradePayload = {
        ...row.payload,
        cost_basis: row.payload.cost_basis ?? synth.cost_basis,
        proceeds: row.payload.proceeds ?? synth.proceeds,
        realized_quantity: row.payload.realized_quantity ?? synth.realized_quantity,
        total_round_trip_quantity:
          row.payload.total_round_trip_quantity ?? synth.total_round_trip_quantity,
      };
      if (synth.realized_pnl !== null) {
        merged.realized_pnl = row.payload.realized_pnl ?? synth.realized_pnl;
      }
      const trade = rowToBlotterTrade({ ...row, payload: merged });
      // Honour the lot-matched is_closed verdict (rowToBlotterTrade's
      // heuristic only flips on action label / non-zero realized_pnl —
      // a SELL that's actually a partial open would slip through).
      if (synth.is_closed) trade.is_closed = true;
      totalCommissions += trade.total_commission || 0;
      if (trade.is_closed) {
        realizedPnl += trade.realized_pnl ?? 0;
        closed.push(trade);
      } else {
        open.push(trade);
      }
      return;
    }

    const trade = rowToBlotterTrade(row);
    totalCommissions += trade.total_commission || 0;
    if (trade.is_closed) {
      realizedPnl += trade.realized_pnl ?? 0;
      closed.push(trade);
    } else {
      open.push(trade);
    }
  });

  return {
    as_of: maxFilledAt(rows),
    summary: {
      closed_trades: closed.length,
      open_trades: open.length,
      total_commissions: totalCommissions,
      realized_pnl: realizedPnl,
    },
    closed_trades: closed,
    open_trades: open,
  };
}
