export type PriceData = {
  symbol: string;
  last: number | null;
  lastIsCalculated: boolean;
  bid: number | null;
  ask: number | null;
  bidSize: number | null;
  askSize: number | null;
  volume: number | null;
  high: number | null;
  low: number | null;
  open: number | null;
  close: number | null;
  // Misc Stats (generic tick 165)
  week52High: number | null;
  week52Low: number | null;
  avgVolume: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  impliedVol: number | null;
  undPrice: number | null;
  // Forward price: for forward-priced indices (VIX) the relay publishes the
  // front-month future last/mid here so option pricing uses the tradeable
  // forward instead of the (after-hours-frozen) cash index. Null otherwise.
  fwd?: number | null;
  // Per-expiry forward curve, keyed by the OPTION expiry (YYYYMMDD) the relay
  // matched to a VIX future. Each held VIX option leg is priced off the future
  // of its own expiry via fwdCurve[legExpiry]; falls back to `fwd` (front
  // month) then cash. The relay owns the option->future matching.
  fwdCurve?: Record<string, number> | null;
  timestamp: string;
};


export type FundamentalsData = {
  symbol: string;
  peRatio: number | null;
  eps: number | null;
  dividendYield: number | null;
  week52High: number | null;
  week52Low: number | null;
  priceBookRatio: number | null;
  roe: number | null;
  revenue: number | null;
  timestamp: string;
};

export type WSPriceMessage = {
  type: "price";
  symbol: string;
  data: PriceData;
};

export type WSSubscribedMessage = {
  type: "subscribed";
  symbols: string[];
};

export type WSUnsubscribedMessage = {
  type: "unsubscribed";
  symbols: string[];
};

export type WSSnapshotMessage = {
  type: "snapshot";
  symbol: string;
  data: PriceData;
};

export type WSFundamentalsMessage = {
  type: "fundamentals";
  symbol: string;
  data: FundamentalsData;
};

export type WSErrorMessage = {
  type: "error";
  message: string;
};

export type WSPingMessage = {
  type: "ping";
};

export type WSPongMessage = {
  type: "pong";
};

export type WSStatusMessage = {
  type: "status";
  ib_connected: boolean;
  ib_issue: string | null;
  ib_status_message: string | null;
  subscriptions: string[];
};

export type WSBatchMessage = {
  type: "batch";
  updates: Record<string, PriceData>;
};

/* ─── Depth-of-book (L2) types ─────────────────────────── */

export type DepthSide = "bid" | "ask";

/** One book row. marketMaker/exchange null for futures (no venue attribution). */
export type DepthLevel = {
  price: number;
  size: number;
  marketMaker: string | null; // MPID (NASDAQ TotalView) — equities direct
  exchange: string | null; // venue code (SMART equities, options BBO)
  /**
   * Options only: this venue row sets the NBBO (best bid / best ask across
   * exchanges). The relay flags it per row; ties at the inside mark every
   * matching venue. Absent on stock/future books, which key best on position.
   */
  nbbo?: boolean;
};

/**
 * Cross-venue NBBO summary for an option montage. The relay derives this from
 * the inside (best bid / best ask) venue rows and attaches it to option books
 * only — it is the authoritative top-of-book for the header on the Book tab.
 */
export type DepthNbbo = {
  bestBid: number | null;
  bestAsk: number | null;
  mid: number | null;
  bidSize: number;
  askSize: number;
};

export type DepthBook = {
  symbol: string; // same keyspace as PriceData.symbol (ticker | optionKey | future)
  kind: "stock" | "option" | "future" | "combo";
  bid: DepthLevel[]; // index 0 = inside/best
  ask: DepthLevel[]; // index 0 = inside/best
  isSmartDepth: boolean; // true equities/options, false futures
  feed: string | null; // head-pill label, e.g. "SMART DEPTH · TOTALVIEW"
  entitled: boolean; // false → render L1 fallback
  /** Options only: cross-venue NBBO summary derived by the relay. */
  nbbo?: DepthNbbo;
  timestamp: string;
};

/** Time & Sales tape row. */
export type Trade = {
  price: number;
  size: number;
  exchange: string | null;
  time: string;
};

export type WSDepthMessage = {
  type: "depth";
  symbol: string;
  data: DepthBook;
};

export type WSDepthBatchMessage = {
  type: "depth-batch";
  updates: Record<string, DepthBook>;
};

export type WSDepthUnavailableMessage = {
  type: "depth-unavailable";
  symbol: string;
  reason: "no-entitlement" | "futures-no-depth" | "recycled";
  code?: number;
};

/**
 * Time & Sales batch. Rides the SAME `subscribe-depth` as the depth channel
 * (the relay seeds the tape for the focused depth symbol — no separate client
 * action). Trades are newest-first within each symbol's array; the hook merges
 * and bounds to the most recent rows per symbol.
 */
export type WSTapeBatchMessage = {
  type: "tape-batch";
  updates: Record<string, Trade[]>;
};

export type WSMessage =
  | WSPriceMessage
  | WSFundamentalsMessage
  | WSSubscribedMessage
  | WSUnsubscribedMessage
  | WSSnapshotMessage
  | WSBatchMessage
  | WSErrorMessage
  | WSPingMessage
  | WSPongMessage
  | WSStatusMessage
  | WSDepthMessage
  | WSDepthBatchMessage
  | WSDepthUnavailableMessage
  | WSTapeBatchMessage;

/* ─── Option contract types & helpers ─────────────────── */

export type OptionContract = {
  symbol: string;
  expiry: string; // YYYYMMDD
  strike: number;
  right: "C" | "P";
};

export function normalizeOptionExpiry(expiry: string): string | null {
  const compact = expiry.trim().replace(/-/g, "");
  return compact.length === 8 ? compact : null;
}

export function normalizeOptionContract(contract: OptionContract): OptionContract | null {
  const symbol = contract.symbol.trim().toUpperCase();
  const expiry = normalizeOptionExpiry(contract.expiry);
  if (!symbol || !expiry || !Number.isFinite(contract.strike) || contract.strike <= 0) {
    return null;
  }
  return {
    symbol,
    expiry,
    strike: contract.strike,
    right: contract.right,
  };
}

/** Build composite key for an option contract: SYMBOL_YYYYMMDD_STRIKE_RIGHT */
export function optionKey(c: OptionContract): string {
  const normalized = normalizeOptionContract(c);
  if (normalized) {
    return `${normalized.symbol}_${normalized.expiry}_${normalized.strike}_${normalized.right}`;
  }
  return `${c.symbol.trim().toUpperCase()}_${c.expiry.trim()}_${c.strike}_${c.right}`;
}

/**
 * Inverse of `optionKey`: parse a composite key `SYMBOL_YYYYMMDD_STRIKE_RIGHT`
 * back into an OptionContract. Returns null for anything that is not a
 * well-formed option key (e.g. a bare stock ticker or futures root), so callers
 * can branch on instrument kind. The symbol segment never contains "_" (tickers
 * are alphanumerics), so splitting on the trailing three "_" segments is safe.
 */
export function parseOptionKey(key: string): OptionContract | null {
  const parts = key.trim().split("_");
  if (parts.length < 4) return null;
  const right = parts[parts.length - 1];
  if (right !== "C" && right !== "P") return null;
  const strike = Number(parts[parts.length - 2]);
  const expiry = parts[parts.length - 3];
  const symbol = parts.slice(0, parts.length - 3).join("_");
  return normalizeOptionContract({ symbol, expiry, strike, right });
}

export function uniqueOptionContracts(contracts: OptionContract[]): OptionContract[] {
  const seen = new Set<string>();
  const normalizedContracts: OptionContract[] = [];
  for (const contract of contracts) {
    const normalized = normalizeOptionContract(contract);
    if (!normalized) continue;
    const key = optionKey(normalized);
    if (seen.has(key)) continue;
    seen.add(key);
    normalizedContracts.push(normalized);
  }
  return normalizedContracts;
}

/** Stable hash for a list of option contracts (for memoization change detection) */
export function contractsKey(contracts: OptionContract[]): string {
  return uniqueOptionContracts(contracts)
    .map(optionKey)
    .sort()
    .join(",");
}

/**
 * Convert a portfolio leg into an IB-ready OptionContract descriptor.
 * Returns null for Stock legs, null/0 strikes, or missing data.
 */
export function portfolioLegToContract(
  ticker: string,
  expiry: string,
  leg: { type: string; strike: number | null },
): OptionContract | null {
  if (leg.type === "Stock") return null;
  if (leg.strike == null || leg.strike === 0) return null;
  if (!expiry || expiry === "N/A") return null;

  const right = leg.type === "Call" ? "C" : leg.type === "Put" ? "P" : null;
  if (!right) return null;

  return normalizeOptionContract({
    symbol: ticker.toUpperCase(),
    expiry,
    strike: leg.strike,
    right,
  });
}

/* ─── Index contract types ────────────────────────────── */

export type IndexContract = {
  symbol: string;
  exchange: string; // e.g. "CBOE"
};

/* ─── Symbol helpers ──────────────────────────────────── */

export function normalizeSymbolList(symbols: string[]): string[] {
  return [...symbols]
    .map((symbol) => symbol.trim().toUpperCase())
    .filter((symbol) => symbol.length > 0);
}

export function symbolKey(symbols: string[]): string {
  return normalizeSymbolList(symbols).sort().join(",");
}
