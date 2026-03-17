/**
 * IB tick handler — pure functions for processing tickPrice/tickSize events.
 * Extracted from ib_realtime_server.js so they can be unit-tested independently.
 */

import IB from "ib";

const { TICK_TYPE } = IB;

export function normalizeNumber(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return null;
  }
  return value;
}

export function createPriceData(symbol) {
  return {
    symbol,
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    // Misc Stats (generic tick 165)
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

// Cash indexes report value via CLOSE tick, not LAST. Stocks should NOT
// fall back to CLOSE — IB's close is the PREVIOUS session's close and can
// be days stale on weekends, giving wildly inaccurate "underlying" prices.
const CASH_INDEX_SYMBOLS = new Set(["VIX", "VVIX", "COR1M", "SPX", "NDX", "RUT", "DJX", "OVX", "MOVE"]);

export function updateDerivedLast(data) {
  if (data.last == null && data.bid != null && data.ask != null) {
    const midpoint = (data.bid + data.ask) / 2;
    data.last = Number.isFinite(midpoint) ? Number(midpoint.toFixed(4)) : null;
    data.lastIsCalculated = true;
  }
  // Only fall back to close for cash indexes — their value IS the close tick.
  // For stocks/options, leave last as null so the UI shows "---" rather than
  // a stale previous-session close masquerading as a current price.
  if (data.last == null && data.close != null) {
    const baseSymbol = data.symbol.split("_")[0];
    if (CASH_INDEX_SYMBOLS.has(baseSymbol)) {
      data.last = data.close;
      data.lastIsCalculated = true;
    }
  }
}

export function updatePriceFromTickPrice(data, tickType, value) {
  switch (tickType) {
    // ── Live tick types ───────────────────────────────────────────────────
    case TICK_TYPE.BID:
      data.bid = normalizeNumber(value);
      data.lastIsCalculated = false;
      break;
    case TICK_TYPE.ASK:
      data.ask = normalizeNumber(value);
      data.lastIsCalculated = false;
      break;
    case TICK_TYPE.LAST:
      data.last = normalizeNumber(value);
      data.lastIsCalculated = false;
      break;
    case TICK_TYPE.HIGH:
      data.high = normalizeNumber(value);
      break;
    case TICK_TYPE.LOW:
      data.low = normalizeNumber(value);
      break;
    case TICK_TYPE.OPEN:
      data.open = normalizeNumber(value);
      break;
    case TICK_TYPE.CLOSE:
      data.close = normalizeNumber(value);
      break;
    case TICK_TYPE.VOLUME:
      data.volume = normalizeNumber(value);
      break;

    // ── Misc Stats (generic tick 165) ─────────────────────────────────────
    case TICK_TYPE.LOW_52_WEEK:    // 19
      data.week52Low = normalizeNumber(value);
      break;
    case TICK_TYPE.HIGH_52_WEEK:   // 20
      data.week52High = normalizeNumber(value);
      break;
    case TICK_TYPE.AVG_VOLUME:     // 21
      data.avgVolume = normalizeNumber(value);
      break;

    // ── Delayed tick types (reqMarketDataType(4) fallback for indexes) ────
    // IB sends these instead of live types when a real-time subscription is
    // absent. VIX/VVIX always receive delayed ticks because they require a
    // separate CBOE index subscription.
    case TICK_TYPE.DELAYED_BID:         // 66
      data.bid = normalizeNumber(value);
      data.lastIsCalculated = false;
      break;
    case TICK_TYPE.DELAYED_ASK:         // 67
      data.ask = normalizeNumber(value);
      data.lastIsCalculated = false;
      break;
    case TICK_TYPE.DELAYED_LAST:        // 68
      data.last = normalizeNumber(value);
      data.lastIsCalculated = false;
      break;
    case TICK_TYPE.DELAYED_HIGH:        // 72
      data.high = normalizeNumber(value);
      break;
    case TICK_TYPE.DELAYED_LOW:         // 73
      data.low = normalizeNumber(value);
      break;
    case TICK_TYPE.DELAYED_VOLUME:      // 74
      data.volume = normalizeNumber(value);
      break;
    case TICK_TYPE.DELAYED_CLOSE:       // 75
      data.close = normalizeNumber(value);
      break;
    case TICK_TYPE.DELAYED_OPEN:        // 76
      data.open = normalizeNumber(value);
      break;

    default:
      break;
  }

  if (data.last == null) {
    updateDerivedLast(data);
  }

  // ── Stale frozen LAST detection for options ──
  // IB with reqMarketDataType(4) sends frozen LAST = yesterday's close before
  // live ticks arrive. For options (symbol contains "_"), if LAST equals CLOSE
  // and bid/ask indicate a very different price (>20% divergence), replace LAST
  // with bid/ask midpoint. Stocks are excluded — last=close is normal after hours.
  if (
    data.last != null &&
    data.close != null &&
    data.last === data.close &&
    data.bid != null &&
    data.ask != null &&
    data.symbol.includes("_") // options only (keyed as SYMBOL_EXPIRY_STRIKE_RIGHT)
  ) {
    const mid = (data.bid + data.ask) / 2;
    const divergence = Math.abs(mid - data.last) / data.last;
    if (divergence > 0.20) {
      data.last = Number(mid.toFixed(4));
      data.lastIsCalculated = true;
    }
  }

  data.timestamp = new Date().toISOString();
}

/* ─── Fundamentals (tickString type 47) ─────────────────────── */

/**
 * IB sentinel for "no value" — DBL_MAX or values > 1e300.
 */
function isSentinel(v) {
  return !Number.isFinite(v) || Math.abs(v) > 1e300;
}

export function createFundamentalsData(symbol) {
  return {
    symbol,
    peRatio: null,
    eps: null,
    dividendYield: null,
    week52High: null,
    week52Low: null,
    priceBookRatio: null,
    roe: null,
    revenue: null,
    timestamp: new Date().toISOString(),
  };
}

/**
 * IB fundamental ratios arrive as semicolon-delimited key=value pairs:
 *   "PEEXCLXOR=25.3;YIELD=1.5;NHIG=185.0;NLOW=120.5;..."
 *
 * Known keys:
 *   PEEXCLXOR  — P/E excluding extraordinary items
 *   TTMEPSXCLX — Trailing 12m EPS excl extra
 *   YIELD      — Dividend yield (%)
 *   NHIG       — 52-week high
 *   NLOW       — 52-week low
 *   MKTCAP     — Market cap (millions)
 *   PRICE2BK   — Price/book ratio
 *   TTMROEPCT  — Trailing 12m ROE (%)
 *   TTMREV     — Trailing 12m revenue
 */
const FUNDAMENTAL_FIELD_MAP = {
  PEEXCLXOR: "peRatio",
  TTMEPSXCLX: "eps",
  YIELD: "dividendYield",
  NHIG: "week52High",
  NLOW: "week52Low",
  PRICE2BK: "priceBookRatio",
  TTMROEPCT: "roe",
  TTMREV: "revenue",
};

export function parseFundamentalRatios(data, fundString) {
  if (typeof fundString !== "string" || fundString.length === 0) return false;

  const pairs = fundString.split(";");
  let updated = false;

  for (const pair of pairs) {
    const eqIdx = pair.indexOf("=");
    if (eqIdx < 1) continue;
    const key = pair.substring(0, eqIdx).trim();
    const field = FUNDAMENTAL_FIELD_MAP[key];
    if (!field) continue;

    const val = parseFloat(pair.substring(eqIdx + 1));
    if (isSentinel(val)) continue;

    data[field] = val;
    updated = true;
  }

  if (updated) {
    data.timestamp = new Date().toISOString();
  }
  return updated;
}

export function updatePriceFromTickSize(data, sizeType, value) {
  switch (sizeType) {
    case TICK_TYPE.BID_SIZE:
      data.bidSize = normalizeNumber(value);
      break;
    case TICK_TYPE.ASK_SIZE:
      data.askSize = normalizeNumber(value);
      break;
    case TICK_TYPE.VOLUME:
      data.volume = normalizeNumber(value);
      break;
    case TICK_TYPE.DELAYED_BID_SIZE:    // 69
      data.bidSize = normalizeNumber(value);
      break;
    case TICK_TYPE.DELAYED_ASK_SIZE:    // 70
      data.askSize = normalizeNumber(value);
      break;
    case TICK_TYPE.DELAYED_VOLUME:      // 74
      data.volume = normalizeNumber(value);
      break;
    case TICK_TYPE.LAST_SIZE:
      break;
    default:
      break;
  }

  data.timestamp = new Date().toISOString();
}
