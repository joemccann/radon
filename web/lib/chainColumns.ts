/**
 * Options-chain column model. One visibility map drives both sides: the call
 * side renders CHAIN_COLUMNS outer → strike, the put side mirrors it.
 *
 * Greeks, IV, bid/ask IV and the model price stream from IB's
 * tickOptionComputation. Rho and Implied are Black-Scholes derived (IB
 * publishes no rho).
 */

export type ChainColumnKey =
  | "delta"
  | "gamma"
  | "theta"
  | "vega"
  | "rho"
  | "iv"
  | "bidIv"
  | "askIv"
  | "model"
  | "implied"
  | "volume"
  | "bid"
  | "mid"
  | "ask"
  | "last";

export type ChainColumnGroup = "Greeks" | "Volatility" | "Valuation" | "Quote";

export type ChainColumn = {
  key: ChainColumnKey;
  /** Compact header text. */
  header: string;
  /** Menu label. */
  label: string;
  group: ChainColumnGroup;
  /** Header tooltip: what the number is and where it comes from. */
  title: string;
};

/**
 * Call-side order, outermost first. The put side renders this reversed, except
 * Bid → Mid → Ask keeps its left-to-right reading order on both sides.
 */
export const CHAIN_COLUMNS: readonly ChainColumn[] = [
  { key: "delta", header: "Δ", label: "Delta", group: "Greeks", title: "Delta (IB model)" },
  { key: "gamma", header: "Γ", label: "Gamma", group: "Greeks", title: "Gamma (IB model)" },
  { key: "theta", header: "Θ", label: "Theta", group: "Greeks", title: "Theta, $ per share per day (IB model)" },
  { key: "vega", header: "Vega", label: "Vega", group: "Greeks", title: "Vega, $ per share per 1 vol point (IB model)" },
  { key: "rho", header: "ρ", label: "Rho", group: "Greeks", title: "Rho, $ per share per 1% rate (Black-Scholes; IB publishes no rho)" },
  { key: "iv", header: "IV", label: "IV", group: "Volatility", title: "Implied volatility (IB model)" },
  { key: "bidIv", header: "Bid IV", label: "Bid IV", group: "Volatility", title: "Implied volatility at the bid (IB)" },
  { key: "askIv", header: "Ask IV", label: "Ask IV", group: "Volatility", title: "Implied volatility at the ask (IB)" },
  { key: "model", header: "Model", label: "Model price", group: "Valuation", title: "IB model theoretical price per share" },
  { key: "implied", header: "Implied", label: "Implied", group: "Valuation", title: "Black-Scholes implied (theoretical) per-share price" },
  { key: "volume", header: "Vol", label: "Volume", group: "Quote", title: "Session volume" },
  { key: "bid", header: "Bid", label: "Bid", group: "Quote", title: "Bid" },
  { key: "mid", header: "Mid", label: "Mid", group: "Quote", title: "Mid" },
  { key: "ask", header: "Ask", label: "Ask", group: "Quote", title: "Ask" },
  { key: "last", header: "Last", label: "Last", group: "Quote", title: "Last trade" },
];

export const CHAIN_COLUMN_GROUPS: readonly ChainColumnGroup[] = ["Greeks", "Volatility", "Valuation", "Quote"];

/** Defaults reproduce the chain as it shipped before the toggle existed. */
export const CHAIN_COLUMN_DEFAULTS: Record<ChainColumnKey, boolean> = {
  delta: true,
  gamma: false,
  theta: false,
  vega: false,
  rho: false,
  iv: true,
  bidIv: false,
  askIv: false,
  model: false,
  implied: true,
  volume: true,
  bid: true,
  mid: true,
  ask: true,
  last: true,
};

export const CHAIN_COLUMNS_TABLE_ID = "options-chain";

export function visibleChainColumns(
  visible: Record<ChainColumnKey, boolean>,
  side: "call" | "put",
): ChainColumn[] {
  const shown = CHAIN_COLUMNS.filter((c) => visible[c.key]);
  if (side === "call") return shown;
  const mirrored = [...shown].reverse();
  const quoteSlots = mirrored.flatMap((c, i) => (QUOTE_ORDER.includes(c.key) ? [i] : []));
  const quoteCols = quoteSlots.map((i) => mirrored[i]).reverse();
  quoteSlots.forEach((slot, n) => {
    mirrored[slot] = quoteCols[n];
  });
  return mirrored;
}

const QUOTE_ORDER: readonly ChainColumnKey[] = ["bid", "mid", "ask"];
