import type { DepthBook, DepthLevel } from "@/lib/pricesProtocol";

/**
 * Microstructure derivation for the L2 order book (F10). Pure, side-effect-free,
 * deterministic. Mirrors the Python scan in `scripts/microstructure.py` 1:1 so
 * the live Book tab and a Python backtest agree on the numbers. Edit one, edit
 * the other.
 *
 * Two signals, both off RESTING depth only — the trade-direction tape is still
 * Phase 3 / empty, so VPIN / order-flow toxicity is deliberately out of scope:
 *
 *  - imbalance: net resting pressure in [-1, 1] over the top-N levels.
 *      (sumBidSize - sumAskSize) / (sumBidSize + sumAskSize)
 *    +1 = all bid, -1 = all ask, 0 = balanced.
 *
 *  - microprice: size-weighted touch price. Each touch price is weighted by the
 *    OPPOSITE side's size so a heavy ask pulls fair value toward the bid.
 *      (bestBid * askSize + bestAsk * bidSize) / (bidSize + askSize)
 */

/** Default depth used for the imbalance sum. Matches the relay's depth ticket. */
export const DEFAULT_MICRO_TOP_N = 5;

export type Microstructure = {
  /** Net resting pressure in [-1, 1], or null when neither side has size. */
  imbalance: number | null;
  /** Size-weighted touch price, or null when a touch / size is missing. */
  microprice: number | null;
  /** Plain ((bestBid + bestAsk) / 2), or null when a touch is missing. */
  mid: number | null;
  /** microprice - mid: where the weighted fair value sits versus the mid. */
  microPremium: number | null;
};

function sumSize(levels: DepthLevel[], topN: number): number {
  return levels.slice(0, topN).reduce((acc, level) => acc + (level.size || 0), 0);
}

function bookImbalance(
  bid: DepthLevel[],
  ask: DepthLevel[],
  topN: number,
): number | null {
  const bidSize = sumSize(bid, topN);
  const askSize = sumSize(ask, topN);
  const total = bidSize + askSize;
  if (total <= 0) return null;
  return (bidSize - askSize) / total;
}

function microprice(
  bestBid: number | null,
  bestAsk: number | null,
  bidSize: number,
  askSize: number,
): number | null {
  if (bestBid == null || bestAsk == null) return null;
  const total = bidSize + askSize;
  if (total <= 0) return null;
  return (bestBid * askSize + bestAsk * bidSize) / total;
}

/**
 * Derive imbalance + microprice from a DepthBook. Returns null when there is no
 * entitled, non-empty book to read (so callers can simply not render the strip).
 */
export function deriveMicrostructure(
  depth: DepthBook | null | undefined,
  topN: number = DEFAULT_MICRO_TOP_N,
): Microstructure | null {
  if (!depth || depth.entitled !== true) return null;
  if (depth.bid.length === 0 && depth.ask.length === 0) return null;

  const imbalance = bookImbalance(depth.bid, depth.ask, topN);

  const bestBid = depth.bid[0]?.price ?? null;
  const bestAsk = depth.ask[0]?.price ?? null;
  const bestBidSize = depth.bid[0]?.size ?? 0;
  const bestAskSize = depth.ask[0]?.size ?? 0;

  const micro = microprice(bestBid, bestAsk, bestBidSize, bestAskSize);
  const mid = bestBid != null && bestAsk != null ? (bestBid + bestAsk) / 2 : null;
  const microPremium = micro != null && mid != null ? micro - mid : null;

  return { imbalance, microprice: micro, mid, microPremium };
}
