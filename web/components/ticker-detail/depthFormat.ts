/**
 * Depth-of-book price formatter. Mirrors the montage mockup's `fmt`: two
 * decimals for whole-cent prices, four for sub-cent fractional ticks (dark
 * prints / midpoint fills). No currency prefix — the montage columns are dense
 * monospace ladders, not standalone money fields.
 */
export function fmtDepthPrice(price: number): string {
  if (price < 10) return price.toFixed(2);
  return Number.isInteger(price * 100) ? price.toFixed(2) : price.toFixed(4);
}

/** Bid/ask spread to two decimals. Returns "---" when either side is missing. */
export function fmtSpread(bid: number | null, ask: number | null): string {
  if (bid == null || ask == null) return "---";
  if (ask < bid) return "CROSSED";
  return (ask - bid).toFixed(2);
}
