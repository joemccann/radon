/**
 * Canonical list of index symbols that IBKR treats as `secType=IND`
 * (not `STK`). Subscribing to these as stocks returns no data;
 * subscribing via the index path (`reqMktData` on `Index(symbol, exchange, currency)`)
 * works.
 *
 * Used by:
 *  - `WorkspaceShell` to route the `/[ticker]` subscription through
 *    `indexes` instead of `symbols` when the ticker is a known index.
 *  - `OrderTab` / `BookTab` to hide trading affordances since indices
 *    themselves are not tradeable (only futures / options on the index).
 *  - `lib/indexSymbols.test.ts` keeps the list in sync with the Python
 *    counterpart at `scripts/utils/index_symbols.py`.
 *
 * IMPORTANT: this is the set of indices Radon currently knows about.
 * Adding a new one requires:
 *   1. Adding it here AND in `scripts/utils/index_symbols.py`
 *   2. Confirming the user's IBKR account has the matching market-data
 *      subscription (CBOE Indexes for VIX/VVIX/SPX, etc.).
 */

export type IndexExchange = "CBOE" | "NASDAQ" | "NYSE" | "RUSSELL";

export const INDEX_SYMBOLS: Record<string, IndexExchange> = {
  // Volatility (CBOE Indexes feed)
  VIX: "CBOE",
  VXN: "CBOE",
  VVIX: "CBOE",
  VXX: "CBOE", // ETN — but tracked like an index for quote purposes here
  COR1M: "CBOE",
  COR3M: "CBOE",
  SKEW: "CBOE",
  // Broad-market indices
  SPX: "CBOE",
  NDX: "NASDAQ",
  RUT: "RUSSELL",
  DJX: "CBOE",
  OEX: "CBOE",
  XSP: "CBOE", // mini-SPX
};

/** True iff the symbol is one IBKR exposes via `secType=IND`. */
export function isIndexSymbol(symbol: string | null | undefined): boolean {
  if (!symbol) return false;
  return symbol.toUpperCase() in INDEX_SYMBOLS;
}

/** Exchange code IBKR expects for an index symbol; null when unknown. */
export function indexExchangeFor(symbol: string | null | undefined): IndexExchange | null {
  if (!symbol) return null;
  const upper = symbol.toUpperCase();
  return INDEX_SYMBOLS[upper] ?? null;
}

/**
 * Symbols where Radon supports trading futures on the underlying index.
 * Keep this in sync with scripts/clients/contract_resolver.py FUTURES_ROOTS.
 * VIX resolves to the VIX/CFE future; SPX/NDX/RUT to their CME E-minis
 * (ES/NQ/RTY) — the index→future mapping lives in the relay + resolver.
 */
const FUTURES_SUPPORTED_SYMBOLS = new Set(["VIX", "SPX", "NDX", "RUT"]);

export function hasFuturesSupport(symbol: string | null | undefined): boolean {
  if (!symbol) return false;
  return FUTURES_SUPPORTED_SYMBOLS.has(symbol.toUpperCase());
}

/**
 * Symbols where Radon supports trading options on the underlying index.
 * Keep in sync with scripts/clients/contract_resolver.py
 * INDEX_OPTION_ROOTS.
 */
/**
 * Indices whose options are priced off a tradeable FUTURE (the forward), not
 * the cash spot. Black-Scholes must use the front-month future as the
 * underlying S for these — the cash index is neither tradeable nor hedgeable,
 * carries a large basis, and freezes after hours. VIX is the clear case (VIX
 * options settle into the VIX future). The relay publishes the forward as
 * prices[symbol].fwd. SPX/NDX/RUT cash track their forwards closely and trade
 * continuously in RTH, so they are intentionally NOT included.
 */
const FORWARD_PRICED_INDICES = new Set(["VIX"]);

export function isForwardPricedIndex(symbol: string | null | undefined): boolean {
  if (!symbol) return false;
  return FORWARD_PRICED_INDICES.has(symbol.toUpperCase());
}

const INDEX_OPTIONS_SUPPORTED_SYMBOLS = new Set(["VIX", "SPX", "NDX", "RUT", "XSP"]);

export function hasIndexOptionsSupport(symbol: string | null | undefined): boolean {
  if (!symbol) return false;
  return INDEX_OPTIONS_SUPPORTED_SYMBOLS.has(symbol.toUpperCase());
}
