/**
 * Depth-of-book price formatter. Mirrors the montage mockup's `fmt`: two
 * decimals for whole-cent prices, four for sub-cent fractional ticks (dark
 * prints / midpoint fills). No currency prefix — the montage columns are dense
 * monospace ladders, not standalone money fields.
 */
export function fmtDepthPrice(price: number): string {
  if (price < 10) return price.toFixed(2);
  return isWholeCent(price) ? price.toFixed(2) : price.toFixed(4);
}

/** 158.8 * 100 is 15880.000000000002 in floating point, so compare against
 *  the nearest cent rather than testing the product for an integer. */
function isWholeCent(price: number): boolean {
  const hundredths = price * 100;
  return Math.abs(hundredths - Math.round(hundredths)) < 1e-6;
}

/**
 * Characters to reserve for a live `fmtDepthPrice` value: the widest string its
 * magnitude can print (4 decimals at >= 10), so a 2- and a 4-decimal tick of
 * the same price occupy the same width and the head does not reflow.
 */
export function depthPriceCh(price: number): number {
  const intDigits = String(Math.trunc(Math.abs(price))).length;
  return (price < 0 ? 1 : 0) + intDigits + 1 + (price < 10 ? 2 : 4);
}

/** Bid/ask spread to two decimals. Returns "---" when either side is missing. */
export function fmtSpread(bid: number | null, ask: number | null): string {
  if (bid == null || ask == null) return "---";
  if (ask < bid) return "CROSSED";
  return (ask - bid).toFixed(2);
}
