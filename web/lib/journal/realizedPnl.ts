/**
 * Realized P&L from raw journal rows — pure module, no I/O.
 *
 * Powers the assistant's `get_realized_pnl` / `query_journal` tools. The
 * Turso `journal` table carries TWO row families for the same executions:
 *
 *   - "flex_agg": Flex-rehydrate aggregate rows — composite ib_exec_id
 *     ("a+b+c") and/or persisted lot-matched fields (cost_basis / proceeds /
 *     open_basis / realized_quantity).
 *   - "fill": realtime-daemon per-fill rows — single exec ids, no basis
 *     fields.
 *
 * The SAME fill appears in BOTH families, so any arithmetic must dedup
 * first (the aggregate wins — it is authoritative and carries the
 * lot-matched fields). Closes lot-match against opens by running VWAP
 * (per-unit basis = open_basis / |qty|, the scripts/clients/journal_basis.py
 * convention), and the matching open may predate the queried window —
 * realized P&L is attributed to the CLOSE date.
 *
 * The inventory matcher below is a deliberate variant of `inventoryPnl` in
 * web/lib/blotter/fromJournal.ts: that one returns per-contract-group
 * TOTALS for the /orders blotter, this one emits dated per-close events so
 * a date window can filter on close day. Do not unify them — fromJournal
 * drives a production surface (surgical-change rule wins over
 * say-it-once here).
 */

import {
  compactExpiry,
  contractGroupKey,
  type JournalRow,
  type JournalTradePayload,
} from "@/lib/blotter/fromJournal";
import { toEtDay } from "@/lib/journal/rangePnl";

export type RowFamily = "flex_agg" | "fill";

/** Journal row as fetched from Turso (trade_id alongside payload/filled_at). */
export type RealizedJournalRow = JournalRow & { trade_id?: string };

export type RealizedRoundTrip = {
  ticker: string;
  desc: string;
  structure?: string;
  /** ET YYYY-MM-DD: earliest contributing open, and the close day. */
  opened: string;
  closed: string;
  qty: number;
  /** Open-side value attributed to the closed quantity (VWAP, commissions folded in). */
  basis: number;
  /** Close-side value (commissions folded in). */
  proceeds: number;
  realized_pnl: number;
  open_outside_window?: true;
};

export type RealizedPnlSummary = {
  from: string;
  to: string;
  ticker?: string;
  total_realized_pnl: number;
  count: number;
  round_trips: RealizedRoundTrip[];
  note: string;
};

export function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function resolveTickerUpper(p: JournalTradePayload): string {
  return (p.ticker || p.symbol || "").toString().toUpperCase();
}

function quantityOf(p: JournalTradePayload): number {
  if (typeof p.contracts === "number") return Math.abs(p.contracts);
  if (typeof p.shares === "number") return Math.abs(p.shares);
  return 0;
}

export function classifyRowFamily(p: JournalTradePayload): RowFamily {
  const execId = (p.ib_exec_id ?? "").toString();
  if (execId.includes("+")) return "flex_agg";
  if (
    typeof p.cost_basis === "number"
    || typeof p.proceeds === "number"
    || typeof p.open_basis === "number"
    || typeof p.realized_quantity === "number"
  ) {
    return "flex_agg";
  }
  return "fill";
}

/** IB appends ".NN" correction suffixes to exec ids; strip before comparing. */
function stripCorrectionSuffix(id: string): string {
  return id.replace(/\.\d{2}$/, "");
}

/**
 * Every constituent exec id referenced by a composite ("a+b+c") aggregate
 * in `rows`. Per-fill rows whose own id lands in this set are duplicates of
 * an aggregate.
 */
function collectCompositeConstituents(rows: RealizedJournalRow[]): Set<string> {
  const constituents = new Set<string>();
  for (const row of rows) {
    const execId = (row.payload?.ib_exec_id ?? "").toString();
    if (!execId.includes("+")) continue;
    for (const part of execId.split("+")) {
      const clean = stripCorrectionSuffix(part.trim());
      if (clean) constituents.add(clean);
    }
  }
  return constituents;
}

function isFillDuplicateOfAggregate(
  p: JournalTradePayload,
  constituents: Set<string>,
): boolean {
  const execId = (p.ib_exec_id ?? "").toString();
  if (!execId || execId.includes("+")) return false;
  return constituents.has(stripCorrectionSuffix(execId));
}

/**
 * Drop (1) per-fill twins of Flex composite aggregates — the aggregate wins,
 * it carries the lot-matched fields — and (2) exact trade_id / exec-id
 * duplicates (window + widened prior fetches can overlap).
 */
export function dedupJournalRows(rows: RealizedJournalRow[]): {
  rows: RealizedJournalRow[];
  droppedFillDupes: number;
} {
  const constituents = collectCompositeConstituents(rows);
  const kept: RealizedJournalRow[] = [];
  const seenIdentity = new Set<string>();
  let droppedFillDupes = 0;

  for (const row of rows) {
    const p = row.payload;
    if (!p || typeof p !== "object") continue;
    if (isFillDuplicateOfAggregate(p, constituents)) {
      droppedFillDupes += 1;
      continue;
    }
    const execId = (p.ib_exec_id ?? "").toString();
    const identity = row.trade_id ? `t:${row.trade_id}` : execId ? `e:${execId}` : "";
    if (identity) {
      if (seenIdentity.has(identity)) continue;
      seenIdentity.add(identity);
    }
    kept.push(row);
  }
  return { rows: kept, droppedFillDupes };
}

/* ── Inventory matcher: dated per-close events ─────────────────────────── */

type MatchExec = {
  side: "BUY" | "SELL";
  qty: number;
  /** Commission-inclusive cash magnitude of the exec: cash OUT for a BUY,
   *  cash IN for a SELL. Taken from the row's authoritative net field —
   *  never reconstructed from fill_price ± commission, because the two row
   *  families store commission signs inconsistently (daemon rows carry
   *  rebates as negatives; Flex rows are always positive). */
  value: number;
  whenRaw: string;
  day: string;
};

type CloseEvent = {
  day: string;
  qty: number;
  basis: number;
  proceeds: number;
  realized: number;
  openDays: string[];
};

function execSide(action: string): "BUY" | "SELL" | null {
  const a = (action || "").toUpperCase();
  if (a === "BUY" || a === "BUY_OPTION" || a === "BUY_TO_CLOSE") return "BUY";
  if (a === "SELL" || a === "SELL_TO_OPEN" || a === "SELL_OPTION") return "SELL";
  return null;
}

function effectiveWhen(row: RealizedJournalRow): string {
  return (row.filled_at || row.payload.filled_at || row.payload.date || "").toString();
}

/**
 * Commission-inclusive cash magnitude of one exec.
 *
 * Field precedence per row family (verified against live rows 2026-07-24):
 * - SELL: Flex aggregates carry net `proceeds` (gross - commission); their
 *   `total_cost` is gross + commission and must NOT be used. Daemon rows
 *   have no `proceeds` but their `total_cost` IS the net credit.
 * - BUY: `total_cost` is the commission-inclusive debit in both families
 *   (daemon rebates arrive as negative commissions already folded in).
 * - Fallback (malformed row): reconstruct as notional +/- |commission|.
 */
function execCashValue(p: JournalTradePayload, side: "BUY" | "SELL", qty: number): number {
  if (side === "SELL" && typeof p.proceeds === "number" && p.proceeds > 0) return p.proceeds;
  if (typeof p.total_cost === "number" && p.total_cost > 0) return p.total_cost;
  const price = typeof p.fill_price === "number" ? p.fill_price : 0;
  const mult = p.contracts != null ? 100 : 1;
  const commission = Math.abs(typeof p.commission === "number" ? p.commission : 0);
  const notional = qty * price * mult;
  return side === "BUY" ? notional + commission : Math.max(0, notional - commission);
}

/**
 * Running-VWAP inventory walk (long and short directions), commissions
 * folded into open/close values exactly as fromJournal's inventoryPnl does.
 * Emits one CloseEvent per closing exec, carrying the ET days of the opens
 * consumed so cross-window opens are attributable.
 */
function matchInventory(execs: MatchExec[]): CloseEvent[] {
  const events: CloseEvent[] = [];
  let positionQty = 0;
  let avgBasis = 0;
  let openDays: string[] = [];

  const sorted = [...execs].sort((a, b) =>
    a.whenRaw < b.whenRaw ? -1 : a.whenRaw > b.whenRaw ? 1 : 0,
  );
  for (const e of sorted) {
    const isBuy = e.side === "BUY";
    const openValue = e.value;
    const signed = isBuy ? e.qty : -e.qty;
    const sameDirection = positionQty === 0 || positionQty > 0 === signed > 0;

    if (sameDirection) {
      const currentBasis = avgBasis * Math.abs(positionQty);
      positionQty += signed;
      avgBasis = positionQty !== 0 ? (currentBasis + openValue) / Math.abs(positionQty) : 0;
      openDays.push(e.day);
      continue;
    }

    const closeQty = Math.min(Math.abs(positionQty), e.qty);
    if (closeQty > 0) {
      const basisClosed = avgBasis * closeQty;
      const closeValue = (e.value / e.qty) * closeQty;
      const realized =
        positionQty > 0 ? closeValue - basisClosed : basisClosed - closeValue;
      events.push({
        day: e.day,
        qty: closeQty,
        basis: basisClosed,
        proceeds: closeValue,
        realized,
        openDays: [...openDays],
      });
      const remaining = Math.abs(positionQty) - closeQty;
      positionQty = remaining > 0 ? (positionQty > 0 ? remaining : -remaining) : 0;
      if (positionQty === 0) {
        avgBasis = 0;
        openDays = [];
      }
    }

    const residual = e.qty - closeQty;
    if (residual > 0) {
      positionQty = isBuy ? residual : -residual;
      avgBasis = e.value / e.qty;
      openDays = [e.day];
    }
  }
  return events;
}

function closeEventsForGroup(rows: RealizedJournalRow[]): CloseEvent[] {
  const events: CloseEvent[] = [];
  const execs: MatchExec[] = [];

  for (const row of rows) {
    const p = row.payload;
    const whenRaw = effectiveWhen(row);
    const day = toEtDay(whenRaw) ?? "";
    if (!day) continue;
    const action = (p.action ?? "").toString().toUpperCase();

    if (action === "CLOSED" && typeof p.realized_pnl === "number") {
      // Net-flat rehydrate aggregate: its VWAP-collapsed price would recover
      // ~0 through the matcher, so pass the persisted round trip through.
      events.push({
        day,
        qty: typeof p.realized_quantity === "number" ? p.realized_quantity : quantityOf(p),
        basis: typeof p.cost_basis === "number" ? p.cost_basis : 0,
        proceeds: typeof p.proceeds === "number" ? p.proceeds : 0,
        realized: p.realized_pnl,
        openDays: [day],
      });
      continue;
    }

    const side = execSide(action);
    const qty = quantityOf(p);
    if (!side || qty <= 0) continue;
    execs.push({
      side,
      qty,
      value: execCashValue(p, side, qty),
      whenRaw,
      day,
    });
  }

  events.push(...matchInventory(execs));
  return events;
}

function contractDescFromKey(key: string, rep: JournalTradePayload): string {
  const ticker = resolveTickerUpper(rep);
  if (key.includes("|OPT|")) {
    const strike = rep.strike != null ? String(rep.strike) : "";
    const right = (rep.right ?? "").toString().toUpperCase();
    return [ticker, `${strike}${right}`, compactExpiry(rep.expiry)]
      .filter(Boolean)
      .join(" ");
  }
  return `${ticker} STK`;
}

function mergeEventsByCloseDay(
  events: CloseEvent[],
  key: string,
  rep: JournalTradePayload,
  from: string,
): RealizedRoundTrip[] {
  const byDay = new Map<string, CloseEvent[]>();
  for (const event of events) {
    const bucket = byDay.get(event.day);
    if (bucket) bucket.push(event);
    else byDay.set(event.day, [event]);
  }

  const out: RealizedRoundTrip[] = [];
  for (const [day, dayEvents] of byDay) {
    const openDays = dayEvents.flatMap((e) => e.openDays).filter(Boolean);
    const opened = openDays.length ? openDays.reduce((a, b) => (a < b ? a : b)) : day;
    const trip: RealizedRoundTrip = {
      ticker: resolveTickerUpper(rep),
      desc: contractDescFromKey(key, rep),
      opened,
      closed: day,
      qty: dayEvents.reduce((s, e) => s + e.qty, 0),
      basis: round2(dayEvents.reduce((s, e) => s + e.basis, 0)),
      proceeds: round2(dayEvents.reduce((s, e) => s + e.proceeds, 0)),
      realized_pnl: round2(dayEvents.reduce((s, e) => s + e.realized, 0)),
    };
    if (typeof rep.structure === "string" && rep.structure) trip.structure = rep.structure;
    if (opened < from) trip.open_outside_window = true;
    out.push(trip);
  }
  return out;
}

/**
 * Deduped, lot-matched realized P&L for closes landing in [from, to]
 * (inclusive ET days). `allRows` should include rows fetched WIDER than the
 * window so cross-window opens can be matched — pre-window round trips are
 * excluded by the close-date filter, not by the caller.
 */
export function computeRealizedPnl(
  allRows: RealizedJournalRow[],
  opts: { from: string; to: string; ticker?: string },
): RealizedPnlSummary {
  const { rows } = dedupJournalRows(allRows);
  const tickerFilter = opts.ticker?.trim().toUpperCase() || undefined;

  const groups = new Map<string, { rep: JournalTradePayload; rows: RealizedJournalRow[] }>();
  for (const row of rows) {
    const p = row.payload;
    if (!p || typeof p !== "object") continue;
    if (tickerFilter && resolveTickerUpper(p) !== tickerFilter) continue;
    const key = contractGroupKey(p);
    if (!key) continue;
    const group = groups.get(key);
    if (group) group.rows.push(row);
    else groups.set(key, { rep: p, rows: [row] });
  }

  const roundTrips: RealizedRoundTrip[] = [];
  for (const [key, group] of groups) {
    const events = closeEventsForGroup(group.rows);
    roundTrips.push(...mergeEventsByCloseDay(events, key, group.rep, opts.from));
  }

  const inWindow = roundTrips
    .filter((trip) => trip.closed >= opts.from && trip.closed <= opts.to)
    .sort((a, b) =>
      a.closed < b.closed ? -1 : a.closed > b.closed ? 1 : a.ticker.localeCompare(b.ticker),
    );

  return {
    from: opts.from,
    to: opts.to,
    ...(tickerFilter ? { ticker: tickerFilter } : {}),
    total_realized_pnl: round2(inWindow.reduce((s, t) => s + t.realized_pnl, 0)),
    count: inWindow.length,
    round_trips: inWindow,
    note: "Net of commissions; deduped across flex_agg/fill row families.",
  };
}
