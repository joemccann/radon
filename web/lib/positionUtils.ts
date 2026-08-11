import type {
  PortfolioPosition,
  PositionReturnCapitalPayloadV2,
} from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";

/* ─── Formatters ──────────────────────────────────────────── */

function normalizeRoundedZero(n: number, fractionDigits: number): number {
  const threshold = 0.5 / (10 ** fractionDigits);
  return Math.abs(n) < threshold ? 0 : n;
}

export const fmtUsd = (n: number) => `$${normalizeRoundedZero(n, 0).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
export const fmtPrice = (n: number) => `$${normalizeRoundedZero(n, 2).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const fmtPriceOrCalculated = (n: number, isCalculated: boolean) => isCalculated ? `C${fmtPrice(n)}` : fmtPrice(n);

type ResolvedRealtimePrice = {
  price: number | null;
  isCalculated: boolean;
};

function isPositiveNumber(value: number | null | undefined): value is number {
  return value != null && Number.isFinite(value) && value > 0;
}

export function resolveRealtimePrice(
  priceData?: PriceData | null,
  fallbackPrice?: number | null,
  fallbackIsCalculated = false,
): ResolvedRealtimePrice {
  const last = isPositiveNumber(priceData?.last) ? priceData.last : null;
  const bid = isPositiveNumber(priceData?.bid) ? priceData.bid : null;
  const ask = isPositiveNumber(priceData?.ask) ? priceData.ask : null;

  if (last != null) {
    // For options (symbol contains "_"): when last is outside the bid-ask
    // spread, the last trade is stale and the live bid/ask better reflects
    // current value. Use mid instead. This catches cases like IWM weeklies
    // where last=3.24 but bid=3.49/ask=3.53 — displaying 3.24 is misleading.
    if (priceData?.symbol?.includes("_") && bid != null && ask != null) {
      const lo = Math.min(bid, ask);
      const hi = Math.max(bid, ask);
      const mid = Number(((bid + ask) / 2).toFixed(4));
      // Case 1: last is outside the bid-ask spread (clearly stale)
      if (last < lo || last > hi) {
        return { price: mid, isCalculated: true };
      }
      // Case 2: last is inside but spread is wide (>10% of mid) and last
      // diverges >5% from mid. Wide spreads make last unreliable — the mid
      // better represents current value. Example: BTU $39P with bid=2.76,
      // ask=3.35, last=2.85 has a 19% spread; last sits near the bottom.
      const spreadPct = (hi - lo) / mid;
      const lastDivergence = Math.abs(last - mid) / mid;
      if (spreadPct > 0.10 && lastDivergence > 0.05) {
        return { price: mid, isCalculated: true };
      }
    }
    return { price: last, isCalculated: Boolean(priceData?.lastIsCalculated) };
  }

  if (bid != null && ask != null) {
    return { price: Number(((bid + ask) / 2).toFixed(4)), isCalculated: true };
  }

  if (isPositiveNumber(fallbackPrice)) {
    return { price: fallbackPrice, isCalculated: fallbackIsCalculated };
  }

  // Final fallback: previous-session close. WS broadcasts close on every tick
  // (with disk-cache backfill in scripts/ib_realtime_server.js), so even on a
  // dead market or a sync that fetched no live quote we still surface the most
  // recent known price instead of "—".
  const close = isPositiveNumber(priceData?.close) ? priceData.close : null;
  if (close != null) {
    return { price: close, isCalculated: true };
  }

  return { price: null, isCalculated: false };
}

/* ─── Position math ───────────────────────────────────────── */

export function resolveMarketValue(pos: PortfolioPosition): number | null {
  // For multi-leg positions, always recompute sign-aware from legs
  if (pos.legs.length > 1) {
    const known = pos.legs.filter((l) => l.market_value != null);
    if (known.length === 0) return null;
    return known.reduce((s, l) => {
      const sign = l.direction === "LONG" ? 1 : -1;
      return s + sign * Math.abs(l.market_value!);
    }, 0);
  }
  if (pos.market_value != null) return pos.market_value;
  const single = pos.legs[0];
  return single?.market_value ?? null;
}

function getLegMultiplier(leg: { type: string }): number {
  return leg.type === "Stock" ? 1 : 100;
}

export function getMultiplier(pos: PortfolioPosition): number {
  return pos.structure_type === "Stock" || pos.legs.some((leg) => leg.type === "Stock") ? 1 : 100;
}

export function resolveEntryCost(pos: PortfolioPosition): number {
  if (pos.legs.length > 1) {
    return pos.legs.reduce((s, l) => {
      const sign = l.direction === "LONG" ? 1 : -1;
      return s + sign * Math.abs(l.entry_cost);
    }, 0);
  }
  return pos.entry_cost;
}

export function getAvgEntry(pos: PortfolioPosition): number {
  const mult = getMultiplier(pos);
  const raw = resolveEntryCost(pos);
  const denom = Math.abs(pos.contracts) * mult;
  // A multi-leg combo carries the net credit/debit sign (credit → negative),
  // per the "credits negative" convention. `Math.abs(pos.contracts)` keeps a
  // short's contract count from re-flipping the premium sign.
  if (pos.legs.length > 1) return raw / denom;

  // Single-leg. A SHORT option's entry is a premium CREDIT → negative, matching
  // the signed LAST PRICE mark (a short META $625 call at $1.74 shows −$1.74).
  // A stock avg entry is a per-instrument PRICE and stays positive regardless of
  // direction (daca786: a short stock at $1,134.97/sh shows +$1,134.97, not −).
  const magnitude = Math.abs(raw) / denom;
  const onlyLeg = pos.legs[0];
  const isStock = pos.structure_type === "Stock" || onlyLeg?.type === "Stock";
  if (isStock) return magnitude;
  const isShort = (onlyLeg?.direction ?? pos.direction) === "SHORT";
  return isShort ? -magnitude : magnitude;
}

export function getInitialValue(pos: PortfolioPosition): number {
  const raw = resolveEntryCost(pos);
  // A multi-leg combo carries the net credit/debit sign (credit → negative).
  if (pos.legs.length > 1) return raw;
  // Single-leg: mirror getAvgEntry. A SHORT option's initial value is a premium
  // CREDIT → negative; a stock stays a positive per-instrument notional; a long
  // option is a debit paid (positive). Sign from leg.direction, not entry_cost
  // (stored as a positive magnitude).
  const magnitude = Math.abs(raw);
  const onlyLeg = pos.legs[0];
  const isStock = pos.structure_type === "Stock" || onlyLeg?.type === "Stock";
  if (isStock) return magnitude;
  const isShort = (onlyLeg?.direction ?? pos.direction) === "SHORT";
  return isShort ? -magnitude : magnitude;
}

export type ReturnCapitalBasis =
  | {
      amount: number;
      kind: "max-risk";
      source: "position.max_risk";
      asOf: null;
      quality: "exact";
    }
  | {
      amount: number;
      kind: "opening-margin";
      source: string;
      asOf: string;
      quality: "exact" | "observed";
    }
  | {
      amount: number;
      kind: "debit-paid";
      source: "position.entry_cost";
      asOf: string | null;
      quality: "exact";
    };

function normalizedRiskProfile(pos: PortfolioPosition): string {
  return String(pos.risk_profile ?? "").trim().toLowerCase();
}

function isVerifiedReturnCapitalV2(
  pos: PortfolioPosition,
  payload: PositionReturnCapitalPayloadV2 | null | undefined,
): payload is PositionReturnCapitalPayloadV2 & {
  linkage: Extract<PositionReturnCapitalPayloadV2["linkage"], { state: "linked" }>;
} {
  if (payload?.version !== 2
      || !Number.isFinite(payload.amount)
      || payload.amount <= 0
      || typeof payload.currency !== "string"
      || payload.currency.trim().length === 0
      || payload.linkage.state !== "linked") return false;

  const linkage = payload.linkage;
  if (!pos.position_instance_id
      || linkage.position_instance_id !== pos.position_instance_id
      || !linkage.account_id
      || (pos.account_id != null && linkage.account_id !== pos.account_id)
      || linkage.exec_ids.length === 0
      || linkage.con_ids.length === 0
      || (linkage.order_refs.length === 0 && linkage.perm_ids.length === 0)
      || linkage.legs.length !== linkage.con_ids.length) return false;

  const uniqueConIds = new Set(linkage.con_ids);
  if (uniqueConIds.size !== linkage.con_ids.length
      || linkage.exec_ids.some((id) => typeof id !== "string" || id.trim().length === 0)
      || linkage.legs.some((leg) => !Number.isInteger(leg.con_id)
        || !uniqueConIds.has(leg.con_id)
        || !Number.isFinite(leg.multiplier)
        || leg.multiplier <= 0
        || leg.currency !== payload.currency)) return false;

  const positionConIds = pos.legs.map((leg) => leg.con_id).filter((id): id is number => Number.isInteger(id));
  if (positionConIds.length !== pos.legs.length
      || positionConIds.length !== linkage.con_ids.length
      || positionConIds.some((id) => !uniqueConIds.has(id))) return false;

  const measuredAt = payload.measurement.measured_at;
  if (!measuredAt || !Number.isFinite(Date.parse(measuredAt))) return false;
  if (payload.measurement.quality === "estimated") return false;
  if (payload.measurement.quality === "observed") {
    const measurement = payload.measurement;
    return measurement.method === "isolated-account-margin-delta"
      && measurement.isolation === "isolated"
      && measurement.observation_id.trim().length > 0
      && measurement.before_sample_id.trim().length > 0
      && measurement.after_sample_id.trim().length > 0
      && measurement.before_sample_id !== measurement.after_sample_id
      && Number.isFinite(measurement.window_seconds)
      && measurement.window_seconds > 0
      && measurement.window_seconds <= 120
      && measurement.concurrent_exec_ids.length === 0;
  }
  return payload.measurement.quality === "exact";
}

function isFullLossDebit(pos: PortfolioPosition, entryCost: number): boolean {
  if (!Number.isFinite(entryCost) || entryCost <= 0) return false;
  if (normalizedRiskProfile(pos) === "defined") return true;
  if (pos.legs.length === 1) {
    const onlyLeg = pos.legs[0];
    if (onlyLeg?.type === "Stock" && onlyLeg.direction === "LONG") return true;
  }
  return pos.legs.length > 0
    && pos.legs.every((leg) => leg.type !== "Stock" && leg.direction === "LONG");
}

/**
 * Resolve the economically valid denominator for an open-position return.
 *
 * Defined-risk max loss wins. Any position without exact max loss may use a
 * positive, isolated broker-observed opening-margin record with complete v2
 * provenance. A projected what-if number is never accepted. Debit paid is used only
 * where it is demonstrably the full loss.
 */
export function resolveReturnCapital(pos: PortfolioPosition): ReturnCapitalBasis | null {
  if (normalizedRiskProfile(pos) === "defined") {
    const maxRisk = pos.max_risk;
    if (maxRisk != null && Number.isFinite(maxRisk) && maxRisk > 0) {
      return {
        amount: maxRisk,
        kind: "max-risk",
        source: "position.max_risk",
        asOf: null,
        quality: "exact",
      };
    }
  }

  const candidate = pos.return_capital
    && "version" in pos.return_capital
    && pos.return_capital.version === 2
    ? pos.return_capital
    : null;
  if (isVerifiedReturnCapitalV2(pos, candidate)) {
    const observed = candidate.measurement.quality === "observed";
    return {
      amount: candidate.amount,
      kind: "opening-margin",
      source: observed ? "ib-account-margin-delta" : candidate.measurement.method,
      asOf: candidate.measurement.measured_at,
      quality: observed ? "observed" : "exact",
    };
  }

  const entryCost = resolveEntryCost(pos);
  if (isFullLossDebit(pos, entryCost)) {
    const entryDate = String(pos.entry_date ?? "").trim();
    return {
      amount: entryCost,
      kind: "debit-paid",
      source: "position.entry_cost",
      asOf: entryDate && entryDate !== "unknown" ? entryDate : null,
      quality: "exact",
    };
  }

  return null;
}

/** Compatibility scalar for callers that only need the denominator amount. */
export function getPnlCapital(pos: PortfolioPosition): number | null {
  return resolveReturnCapital(pos)?.amount ?? null;
}

export function describeReturnCapital(basis: ReturnCapitalBasis | null): string {
  if (!basis) return "Return unavailable: no verified capital basis";
  const amount = fmtUsd(basis.amount);
  if (basis.kind === "max-risk") return `Return on max risk · ${amount} · exact`;
  if (basis.kind === "debit-paid") {
    return `Return on debit paid · ${amount} · exact${basis.asOf ? ` · as of ${basis.asOf}` : ""}`;
  }
  if (basis.quality === "observed") {
    return `Return on isolated broker-observed opening margin · ${amount} · as of ${basis.asOf}`;
  }
  return `Return on exact opening capital · ${amount} · as of ${basis.asOf}`;
}

/** Unrealized P&L $ = market value − signed entry cost. */
export function getPnlDollars(
  pos: PortfolioPosition,
  marketValue?: number | null,
): number | null {
  const mv = marketValue !== undefined ? marketValue : resolveMarketValue(pos);
  if (mv == null) return null;
  return mv - resolveEntryCost(pos);
}

/**
 * Open-position return = P&L $ / verified risk capital × 100.
 */
export function getPnlPct(
  pos: PortfolioPosition,
  marketValue?: number | null,
): number | null {
  const pnl = getPnlDollars(pos, marketValue);
  const basis = resolveReturnCapital(pos);
  if (pnl == null || basis == null) return null;
  return (pnl / basis.amount) * 100;
}

export function getLastPrice(pos: PortfolioPosition): number | null {
  const mv = resolveMarketValue(pos);
  if (mv == null) return null;
  const mult = getMultiplier(pos);
  return mv / (pos.contracts * mult);
}

export function getLastPriceIsCalculated(pos: PortfolioPosition): boolean {
  if (pos.market_price_is_calculated != null) return pos.market_price_is_calculated;
  if (pos.legs.length === 1) {
    return Boolean(pos.legs[0]?.market_price_is_calculated);
  }
  return pos.legs.some((leg) => Boolean(leg.market_price_is_calculated));
}

/* ─── Price key resolution ────────────────────────────────── */

/**
 * Build a composite price key for a leg within a position.
 * Returns null for Stock legs or missing data.
 */
export function legPriceKey(
  ticker: string,
  expiry: string,
  leg: { type: string; strike: number | null },
): string | null {
  if (leg.type === "Stock") return null;
  if (leg.strike == null || leg.strike === 0) return null;
  if (!expiry || expiry === "N/A") return null;
  const right = leg.type === "Call" ? "C" : leg.type === "Put" ? "P" : null;
  if (!right) return null;
  const expiryClean = expiry.replace(/-/g, "");
  if (expiryClean.length !== 8) return null;
  return optionKey({ symbol: ticker.toUpperCase(), expiry: expiryClean, strike: leg.strike, right });
}

/* ─── Spread net price resolution ─────────────────────────── */

/**
 * Compute synthetic PriceData for a multi-leg spread from per-leg WS prices.
 * Returns null for single-leg, stock positions, or when leg prices are unavailable.
 */
export function resolveSpreadPriceData(
  ticker: string,
  position: PortfolioPosition,
  prices: Record<string, PriceData>,
): PriceData | null {
  if (position.structure_type === "Stock") return null;
  if (position.legs.length < 2) return null;

  let netBid = 0;
  let netAsk = 0;
  for (const leg of position.legs) {
    const key = legPriceKey(ticker, position.expiry, leg);
    if (!key) return null;
    const lp = prices[key];
    if (!lp || lp.bid == null || lp.ask == null) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    netBid += sign * lp.bid;
    netAsk += sign * lp.ask;
  }

  const lo = Math.round(Math.min(netBid, netAsk) * 100) / 100;
  const hi = Math.round(Math.max(netBid, netAsk) * 100) / 100;
  const mid = Number((((lo + hi) / 2)).toFixed(2));

  return {
    symbol: ticker,
    last: mid,
    lastIsCalculated: true,
    bid: lo,
    ask: hi,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: new Date().toISOString(),
  };
}

/* ─── Same-day position detection ─────────────────────────── */

/** Return today's date in ET (YYYY-MM-DD). */
function todayInET(): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** True when the position was opened today. Yesterday's close is
 *  meaningless as a baseline because the position didn't exist. */
function parseDateOnly(rawDate: string | undefined): string | null {
  if (!rawDate) return null;
  const trimmed = rawDate.trim();
  const inlineMatch = trimmed.match(/^(\d{4}-\d{2}-\d{2})/);
  if (inlineMatch) return inlineMatch[1];

  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(parsed);
  const get = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** True when the position was opened today. Yesterday's close is
 *  meaningless as a baseline because the position didn't exist. */
export function isSameDay(pos: PortfolioPosition): boolean {
  const entryDate = parseDateOnly(pos.entry_date);
  return entryDate != null && entryDate === todayInET();
}

/** Compute real-time market value from WS prices for a stock position.
 *  Sign-aware: `pos.contracts` is a positive magnitude, so a SHORT's market
 *  value must read negative for `mv − entry_cost` to be a signed P&L. */
function computeStockRtMv(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  const last = prices?.[pos.ticker]?.last;
  if (last == null || last <= 0) return null;
  return positionDirectionSign(pos) * last * Math.abs(pos.contracts);
}

/** Compute real-time market value from WS prices for option positions. */
function computeRtMv(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock" || !prices) return null;
  let rtMv = 0;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key ? prices[key] : null;
    const current = resolveRealtimePrice(lp, leg.market_price, Boolean(leg.market_price_is_calculated)).price;
    if (current == null) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    rtMv += sign * current * leg.contracts * getLegMultiplier(leg);
  }
  return rtMv;
}

/* ─── Option daily change ─────────────────────────────────── */

export function getOptionDailyChg(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock" || !prices) return null;

  // Same-day position: the position didn't exist yesterday, so yesterday's
  // close is meaningless. Use entry cost as baseline and denominator.
  // Ignore IB daily P&L for same-day positions, which can be stale/inaccurate
  // when the entire position was opened today.
  if (isSameDay(pos)) {
    const ec = resolveEntryCost(pos);
    if (ec === 0) return null;
    const rtMv = computeRtMv(pos, prices);
    const mv = rtMv ?? resolveMarketValue(pos);
    if (mv == null) return null;
    return ((mv - ec) / Math.abs(ec)) * 100;
  }

  // Compute WS close-based daily P&L and close value (needed for % calc)
  let wsDailyPnl = 0;
  let closeValue = 0;
  let hasClose = false;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key ? prices[key] : null;
    const current = resolveRealtimePrice(lp, leg.market_price, Boolean(leg.market_price_is_calculated)).price;
    if (current == null) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    if (lp?.close != null && lp.close > 0) {
      const multiplier = getLegMultiplier(leg);
      wsDailyPnl += sign * (current - lp.close) * leg.contracts * multiplier;
      closeValue += sign * lp.close * leg.contracts * multiplier;
      hasClose = true;
    }
  }
  if (!hasClose || closeValue === 0) return null;

  // Prefer IB's per-position daily P&L (handles intraday additions correctly)
  const effectivePnl = pos.ib_daily_pnl != null ? pos.ib_daily_pnl : wsDailyPnl;
  return (effectivePnl / Math.abs(closeValue)) * 100;
}

/* ─── Stock daily change ──────────────────────────────────── */

/**
 * Day Chg % for an equity position.
 *
 * Overnight: the instrument's own move off yesterday's close.
 * Same-day: yesterday's close is meaningless (the position didn't exist), so
 * the entry cost is the baseline and the reading becomes the position's return
 * since entry — matching the same-day rule already applied to options.
 */
export function getStockDailyChg(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type !== "Stock") return null;

  if (isSameDay(pos)) {
    const entryCost = resolveEntryCost(pos);
    if (entryCost === 0) return null;
    const todayPnl = getTodayPnlDollars(pos, prices);
    if (todayPnl == null) return null;
    return (todayPnl / Math.abs(entryCost)) * 100;
  }

  const p = prices?.[pos.ticker];
  if (!p || p.last == null || p.last <= 0 || p.close == null || p.close <= 0) return null;
  return ((p.last - p.close) / p.close) * 100;
}

/** Direction sign for a position: +1 long, -1 short. A stock's direction lives
 *  on the position/leg, NOT in `pos.contracts` (which is a positive magnitude),
 *  so any `price × contracts` value MUST be multiplied by this to be
 *  sign-correct. Without it a short reads with long's sign — e.g. a short whose
 *  price ROSE shows a gain instead of a loss. Defaults to +1 (long) unless the
 *  direction explicitly reads SHORT, so a long is never mis-signed. */
export function positionDirectionSign(pos: PortfolioPosition): number {
  const dir = (pos.legs[0]?.direction ?? pos.direction ?? "").toUpperCase();
  return dir === "SHORT" ? -1 : 1;
}

/* ─── Today's P&L (dollars) ──────────────────────────────── */

export function getTodayPnlDollars(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock") {
    // Same-day position: Today's P&L = Total P&L (the shares didn't exist
    // yesterday, so the pre-entry move belongs to the market, not the operator).
    if (isSameDay(pos)) {
      const mv = computeStockRtMv(pos, prices) ?? resolveMarketValue(pos);
      return getPnlDollars(pos, mv);
    }
    const p = prices?.[pos.ticker];
    if (!p || p.last == null || p.last <= 0 || p.close == null || p.close <= 0) return null;
    // Sign-aware: a SHORT loses when price rises. `pos.contracts` is a positive
    // magnitude, so apply the direction sign (long stays +; short flips).
    return positionDirectionSign(pos) * (p.last - p.close) * Math.abs(pos.contracts);
  }

  // Same-day position: Today's P&L = Total P&L (position didn't exist yesterday)
  if (isSameDay(pos)) {
    const rtMv = computeRtMv(pos, prices);
    const mv = rtMv ?? resolveMarketValue(pos);
    if (mv == null) return null;
    return mv - resolveEntryCost(pos);
  }

  // Prefer IB's per-position daily P&L for overnight positions
  if (pos.ib_daily_pnl != null) return pos.ib_daily_pnl;

  // Fall back to WS close-based calculation (overnight positions)
  let pnl = 0;
  let hasClose = false;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key && prices ? prices[key] : null;
    const last = (lp?.last != null && lp.last > 0) ? lp.last : (leg.market_price != null && leg.market_price > 0 ? leg.market_price : null);
    if (last == null) return null;
    const close = lp?.close;
    if (close != null && close > 0) {
      const sign = leg.direction === "LONG" ? 1 : -1;
      pnl += sign * (last - close) * leg.contracts * getLegMultiplier(leg);
      hasClose = true;
    }
  }
  return hasClose ? pnl : null;
}
