/**
 * Implied (Black-Scholes) value resolution.
 *
 * Uses streaming WS data (impliedVol from IB tickOptionComputation, latest
 * underlying spot from prices[ticker].last) to compute a per-leg theoretical
 * price. Aggregates across multi-leg combos to a signed per-share net.
 *
 * Source-of-truth math: web/lib/blackScholes.ts (Python parity vs.
 * scripts/scenario_analysis.py).
 */

import { bsPrice, RISK_FREE_RATE_DEFAULT } from "./blackScholes";
import { optionKey, type PriceData } from "./pricesProtocol";
import type { OpenOrder, PortfolioLeg, PortfolioPosition } from "./types";

const MS_PER_DAY = 86_400_000;
const DAYS_PER_YEAR = 365;
/** 16:00 America/New_York ≈ 20:00 UTC during EDT, 21:00 UTC during EST.
 *  Sub-day precision is irrelevant for short-DTE BS pricing where r=0;
 *  we anchor at 20:00 UTC and accept ≤1h error. */
const EXPIRY_HOUR_UTC = 20;

export type LegImpliedInput = {
  ticker: string;
  /** YYYY-MM-DD or YYYYMMDD */
  expiry: string;
  strike: number;
  type: "Call" | "Put";
  direction: "LONG" | "SHORT";
  contracts: number;
};

export type ImpliedValueInputs = {
  S: number;
  K: number;
  T: number;
  sigma: number;
  r: number;
  spotSource: "last" | "undPrice" | "mid";
};

export type ImpliedValueResult = {
  /** Per-share theoretical price (positive). null if any input is unresolvable. */
  perContract: number | null;
  /** Per-leg notional = perContract × contracts × 100. Unsigned. */
  notional: number | null;
  inputs: ImpliedValueInputs | null;
};

export type ImpliedValueOpts = {
  now?: Date;
  riskFreeRate?: number;
};

/* ─── time-to-expiry ─────────────────────────────────── */

function parseExpiryToUtcMs(expiry: string): number | null {
  const compact = expiry.replace(/-/g, "");
  if (compact.length !== 8) return null;
  const y = Number(compact.slice(0, 4));
  const mo = Number(compact.slice(4, 6));
  const d = Number(compact.slice(6, 8));
  if (!Number.isFinite(y) || !Number.isFinite(mo) || !Number.isFinite(d)) return null;
  return Date.UTC(y, mo - 1, d, EXPIRY_HOUR_UTC, 0, 0);
}

export function yearsToExpiry(expiry: string, now: Date): number | null {
  const ms = parseExpiryToUtcMs(expiry);
  if (ms == null) return null;
  const diffMs = ms - now.getTime();
  if (!Number.isFinite(diffMs)) return null;
  return Math.max(0, diffMs / MS_PER_DAY / DAYS_PER_YEAR);
}

/* ─── spot resolution ────────────────────────────────── */

function isPositive(n: number | null | undefined): n is number {
  return typeof n === "number" && Number.isFinite(n) && n > 0;
}

type SpotResolution = { S: number; source: "last" | "undPrice" | "mid" } | null;

function resolveSpot(
  ticker: string,
  optionPd: PriceData | null | undefined,
  prices: Record<string, PriceData>,
): SpotResolution {
  const tickerPd = prices[ticker.toUpperCase()];
  if (isPositive(tickerPd?.last)) return { S: tickerPd!.last!, source: "last" };
  if (isPositive(optionPd?.undPrice)) return { S: optionPd!.undPrice!, source: "undPrice" };
  if (isPositive(tickerPd?.bid) && isPositive(tickerPd?.ask)) {
    return { S: (tickerPd!.bid! + tickerPd!.ask!) / 2, source: "mid" };
  }
  return null;
}

/* ─── leg-level ──────────────────────────────────────── */

const NULL_RESULT: ImpliedValueResult = { perContract: null, notional: null, inputs: null };

function legOptionKey(input: LegImpliedInput): string | null {
  if (!Number.isFinite(input.strike) || input.strike <= 0) return null;
  const compact = input.expiry.replace(/-/g, "");
  if (compact.length !== 8) return null;
  return optionKey({
    symbol: input.ticker.toUpperCase(),
    expiry: compact,
    strike: input.strike,
    right: input.type === "Call" ? "C" : "P",
  });
}

export function computeLegImpliedValue(
  input: LegImpliedInput,
  prices: Record<string, PriceData>,
  opts: ImpliedValueOpts = {},
): ImpliedValueResult {
  const oKey = legOptionKey(input);
  if (!oKey) return NULL_RESULT;
  const optionPd = prices[oKey];
  if (!optionPd || !isPositive(optionPd.impliedVol)) return NULL_RESULT;

  const spot = resolveSpot(input.ticker, optionPd, prices);
  if (!spot) return NULL_RESULT;

  const now = opts.now ?? new Date();
  const T = yearsToExpiry(input.expiry, now);
  if (T == null) return NULL_RESULT;

  const r = opts.riskFreeRate ?? RISK_FREE_RATE_DEFAULT;
  const perContract = bsPrice({
    S: spot.S,
    K: input.strike,
    T,
    r,
    sigma: optionPd.impliedVol!,
    type: input.type,
  });

  if (!Number.isFinite(perContract) || perContract < 0) return NULL_RESULT;

  return {
    perContract,
    notional: perContract * Math.max(0, input.contracts) * 100,
    inputs: { S: spot.S, K: input.strike, T, sigma: optionPd.impliedVol!, r, spotSource: spot.source },
  };
}

/* ─── position-level (combo) ─────────────────────────── */

export type PositionImpliedValueResult = {
  perLeg: ImpliedValueResult[];
  /** Signed per-share net: +long, -short, summed across legs. null if any leg fails. */
  netPerContract: number | null;
  /** Sum of signed leg notionals. */
  netNotional: number | null;
};

const POSITION_NULL: PositionImpliedValueResult = { perLeg: [], netPerContract: null, netNotional: null };

function legToInput(ticker: string, expiry: string, leg: PortfolioLeg): LegImpliedInput | null {
  if (leg.type === "Stock") return null;
  if (leg.strike == null || leg.strike === 0) return null;
  if (leg.type !== "Call" && leg.type !== "Put") return null;
  return {
    ticker,
    expiry,
    strike: leg.strike,
    type: leg.type,
    direction: leg.direction,
    contracts: leg.contracts,
  };
}

export function computePositionImpliedValue(
  position: PortfolioPosition,
  prices: Record<string, PriceData>,
  opts: ImpliedValueOpts = {},
): PositionImpliedValueResult {
  if (position.structure_type === "Stock") return POSITION_NULL;
  if (!position.legs || position.legs.length === 0) return POSITION_NULL;

  const perLeg: ImpliedValueResult[] = [];
  let netPerContract = 0;
  let netNotional = 0;

  for (const leg of position.legs) {
    const input = legToInput(position.ticker, position.expiry, leg);
    if (!input) return POSITION_NULL;

    const result = computeLegImpliedValue(input, prices, opts);
    if (result.perContract == null || result.notional == null) return POSITION_NULL;

    perLeg.push(result);
    const sign = leg.direction === "LONG" ? 1 : -1;
    netPerContract += sign * result.perContract;
    netNotional += sign * result.notional;
  }

  return { perLeg, netPerContract, netNotional };
}

/* ─── order-level (open orders) ──────────────────────── */

export type OrderImpliedValueResult = {
  perLeg: ImpliedValueResult[];
  /** Signed per-share net normalized to combo base quantity (mirrors resolveOpenOrderComboPrice). */
  netPerContract: number | null;
};

const ORDER_NULL: OrderImpliedValueResult = { perLeg: [], netPerContract: null };

function orderToInput(order: OpenOrder): LegImpliedInput | null {
  const c = order.contract;
  if (c.secType !== "OPT") return null;
  if (c.strike == null || !c.right || !c.expiry) return null;
  const compact = c.expiry.replace(/-/g, "");
  if (compact.length !== 8) return null;
  const type: "Call" | "Put" | null =
    c.right === "C" || c.right === "CALL" ? "Call" : c.right === "P" || c.right === "PUT" ? "Put" : null;
  if (!type) return null;
  return {
    ticker: c.symbol,
    expiry: compact,
    strike: c.strike,
    type,
    direction: order.action === "BUY" ? "LONG" : "SHORT",
    contracts: Math.abs(order.totalQuantity),
  };
}

export function computeOrderImpliedValue(
  orders: OpenOrder[],
  prices: Record<string, PriceData>,
  opts: ImpliedValueOpts = {},
): OrderImpliedValueResult {
  if (orders.length === 0) return ORDER_NULL;

  const sizes = orders.map((o) => Math.abs(o.totalQuantity)).filter((q) => q > 0);
  if (sizes.length === 0) return ORDER_NULL;
  const base = Math.min(...sizes);

  const perLeg: ImpliedValueResult[] = [];
  let net = 0;

  for (const order of orders) {
    const input = orderToInput(order);
    if (!input) return ORDER_NULL;

    const result = computeLegImpliedValue(input, prices, opts);
    if (result.perContract == null) return ORDER_NULL;

    perLeg.push(result);
    const sign = order.action === "BUY" ? 1 : -1;
    const scale = Math.abs(order.totalQuantity) / base;
    if (!Number.isFinite(scale) || scale <= 0) return ORDER_NULL;
    net += sign * result.perContract * scale;
  }

  if (!Number.isFinite(net)) return ORDER_NULL;
  return { perLeg, netPerContract: Math.round(net * 100) / 100 };
}
