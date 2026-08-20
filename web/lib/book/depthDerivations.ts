import type { DepthBook, DepthLevel, Trade } from "@/lib/pricesProtocol";

/**
 * Pure, side-effect-free derivations for the L2 order book.
 *
 * These mirror the render math in the montage mockup 1:1 so the component layer
 * stays a thin presentation shell. Everything here is deterministic: same input,
 * same output, no clocks, no globals. Tested in isolation under Node.
 */

/**
 * Mark the first row of each distinct price so the montage can draw the
 * per-price-level edge marker. Input is already ordered best -> worst, so a
 * level boundary is simply a price that differs from the row above it. The
 * very first row always starts a level.
 */
export function groupPriceLevels(
  rows: DepthLevel[],
): Array<DepthLevel & { firstOfLevel: boolean }> {
  let previousPrice: number | null = null;
  return rows.map((row) => {
    const firstOfLevel = row.price !== previousPrice;
    previousPrice = row.price;
    return { ...row, firstOfLevel };
  });
}

/**
 * Per-level intensity in [0,1] for the resting-size depth bar. Scaled against
 * the deepest single level on screen so the busiest row reads as full. A
 * non-positive maxSize means there is nothing to scale against, so every row is
 * empty rather than dividing by zero.
 */
export function montageFill(level: DepthLevel, maxSize: number): number {
  if (maxSize <= 0) return 0;
  return level.size / maxSize;
}

/** A live ladder row carries a real depth level and its derived bar geometry. */
export type LadderLevelRow = {
  level: DepthLevel;
  cum: number;
  fill: number;
  isBest: boolean;
  placeholder?: false;
};

/** A spacer row holds the spine in place when fewer live levels than rows
 *  exist. It renders as an empty, non-interactive cell of full row height. */
export type LadderPlaceholderRow = {
  level: null;
  cum: 0;
  fill: 0;
  isBest: false;
  placeholder: true;
};

export type LadderRow = LadderLevelRow | LadderPlaceholderRow;

export type LadderRows = {
  askRows: LadderRow[];
  bidRows: LadderRow[];
  maxCumulative: number;
};

/** Default rows per side. Picked so the inside market sits well clear of both
 *  ends of the panel and the spine never lands at the very top or bottom. */
export const DEFAULT_LADDER_ROWS = 10;

const PLACEHOLDER_ROW: LadderPlaceholderRow = {
  level: null,
  cum: 0,
  fill: 0,
  isBest: false,
  placeholder: true,
};

/**
 * Build the centered futures DOM ladder with a FIXED row count per side so the
 * inside market (the spine) stays anchored at a constant vertical offset no
 * matter how many live levels stream tick to tick. Cumulative size accrues from
 * the inside out on each side so a resting wall reads as a long bar regardless
 * of how far from the touch it sits. fill normalises each cumulative against the
 * deepest cumulative across both sides — placeholders are excluded so the bars
 * keep one honest scale.
 *
 * Each side emits EXACTLY `rows` entries:
 *  - asks render worst -> best top-down to the spine. We take the best `rows`
 *    ask levels and pad the TOP (far-from-touch end) with placeholders, so the
 *    best ask always sits in the row directly ABOVE the spine.
 *  - bids render best -> worst below the spine. We take the best `rows` bid
 *    levels and pad the BOTTOM with placeholders, so the best bid always sits
 *    directly BELOW the spine.
 */
export function buildLadderRows(
  book: { bid: DepthLevel[]; ask: DepthLevel[] },
  rows: number = DEFAULT_LADDER_ROWS,
): LadderRows {
  const bids = book.bid.slice(0, rows);
  const asks = book.ask.slice(0, rows);

  const bidCum = runningCumulative(bids);
  const askCum = runningCumulative(asks);
  const maxCumulative = Math.max(...bidCum, ...askCum, 1);

  const toRow = (
    level: DepthLevel,
    index: number,
    cum: number,
  ): LadderLevelRow => ({
    level,
    cum,
    fill: cum / maxCumulative,
    isBest: index === 0,
    placeholder: false,
  });

  // Asks: best -> worst becomes worst -> best after reverse, then pad the top.
  const askLevelRows = asks
    .map((level, index) => toRow(level, index, askCum[index]))
    .reverse();
  const askRows = padTop(askLevelRows, rows);

  // Bids: best -> worst as emitted, then pad the bottom.
  const bidLevelRows = bids.map((level, index) =>
    toRow(level, index, bidCum[index]),
  );
  const bidRows = padBottom(bidLevelRows, rows);

  return { askRows, bidRows, maxCumulative };
}

/** Pad the FAR-from-touch end (top) so the inside row stays last (adjacent to
 *  the spine). Truncated to `rows` when already full. */
function padTop(levelRows: LadderRow[], rows: number): LadderRow[] {
  const missing = Math.max(0, rows - levelRows.length);
  return [...Array(missing).fill(PLACEHOLDER_ROW), ...levelRows];
}

/** Pad the FAR-from-touch end (bottom) so the inside row stays first (adjacent
 *  to the spine). Truncated to `rows` when already full. */
function padBottom(levelRows: LadderRow[], rows: number): LadderRow[] {
  const missing = Math.max(0, rows - levelRows.length);
  return [...levelRows, ...Array(missing).fill(PLACEHOLDER_ROW)];
}

export type TickTone = "up" | "down" | "flat";

/**
 * Apply the tick test to a Time & Sales tape: each trade is up/down relative to
 * the price immediately before it, flat on an equal price. The first trade has
 * no predecessor and is flat by convention (we never fabricate a prior price).
 */
export function classifyTicks(
  trades: Trade[],
): Array<Trade & { tone: TickTone }> {
  let previousPrice: number | null = null;
  return trades.map((trade) => {
    const tone: TickTone =
      previousPrice == null
        ? "flat"
        : trade.price > previousPrice
          ? "up"
          : trade.price < previousPrice
            ? "down"
            : "flat";
    previousPrice = trade.price;
    return { ...trade, tone };
  });
}

/**
 * Whether a row is the inside (best) quote for its side. Stock and future books
 * are position-ordered, so the inside row is index 0. Option books are a
 * per-exchange BBO montage where the NBBO-setting venues are flagged explicitly,
 * so we trust the nbbo flag instead of position.
 */
export function isBestLevel(
  level: DepthLevel,
  index: number,
  kind: "stock" | "option" | "future" | "combo",
): boolean {
  if (kind === "option" || kind === "combo") return level.nbbo === true;
  return index === 0;
}

export type BookHeader = {
  bid: number | null;
  ask: number | null;
  /** Depth-derived mid ((bestBid+bestAsk)/2) — the authoritative "MARK" on the
   *  Book tab when an entitled depth book is present. */
  last: number | null;
  /** "MID" when derived from the book, "LAST"/"MARK" when from the L1 feed. */
  lastLabel: string;
};

/**
 * Best bid (max across the book.bid rows) / best ask (min across book.ask).
 * For options this is the NBBO across venue rows; for stocks index 0 is already
 * the inside, and max/min still resolve it. Returns null on an empty side.
 */
function bestBidPrice(rows: DepthLevel[]): number | null {
  if (rows.length === 0) return null;
  return Math.max(...rows.map((row) => row.price));
}

function bestAskPrice(rows: DepthLevel[]): number | null {
  if (rows.length === 0) return null;
  return Math.min(...rows.map((row) => row.price));
}

/** True when both sides form a tradeable market (ask at or above bid, positive). */
export function isValidMarket(bid: number | null, ask: number | null): boolean {
  return bid != null && ask != null && bid > 0 && ask > 0 && ask >= bid;
}

/** IBKR overnight session MPIDs (IBEOS, OVERNIGHT, …). */
const OVERNIGHT_MPID = /^(IBEOS|OVERN)/i;

function isOvernightVenue(row: DepthLevel): boolean {
  return OVERNIGHT_MPID.test(row.marketMaker ?? "") || OVERNIGHT_MPID.test(row.exchange ?? "");
}

function bboFromRows(
  bids: DepthLevel[],
  asks: DepthLevel[],
): { bid: number | null; ask: number | null } {
  return { bid: bestBidPrice(bids), ask: bestAskPrice(asks) };
}

/**
 * SMART depth after hours mixes leftover lit (ARCA/NSDQ) quotes with live
 * overnight (IBEOS) quotes. Max-bid/min-ask across that mix is a crossed
 * composite, not a tradeable BBO. Prefer an uncrossed overnight book, then
 * an uncrossed lit book, then a valid L1 quote.
 */
function uncrossStockBbo(
  depth: DepthBook,
  l1: { bid: number | null; ask: number | null },
): { bid: number | null; ask: number | null } {
  const full = bboFromRows(depth.bid, depth.ask);
  if (isValidMarket(full.bid, full.ask)) return full;

  const overnight = bboFromRows(
    depth.bid.filter(isOvernightVenue),
    depth.ask.filter(isOvernightVenue),
  );
  if (isValidMarket(overnight.bid, overnight.ask)) return overnight;

  const lit = bboFromRows(
    depth.bid.filter((row) => !isOvernightVenue(row)),
    depth.ask.filter((row) => !isOvernightVenue(row)),
  );
  if (isValidMarket(lit.bid, lit.ask)) return lit;

  if (isValidMarket(l1.bid, l1.ask)) return { bid: l1.bid, ask: l1.ask };
  return full;
}

/**
 * Resolve the window-head bid / ask / mark for the Book tab.
 *
 * When an entitled depth book is present it is the authoritative source (a
 * corrupt or negative L1 scalar is ignored): bid/ask come from the book itself
 * and the MARK is the depth mid, labelled "MID". Futures key the inside on
 * position (index 0); options use the relay `nbbo` summary. Stocks take the
 * best uncrossed BBO (overnight MPIDs when SMART mixes leftover lit quotes
 * with IBEOS after hours). Never report a negative spread as a live market.
 *
 * Falls back entirely to the passed-in L1 scalars when there is no entitled
 * depth book (the L1 fallback path).
 */
export function deriveBookHeader(
  depth: DepthBook | null | undefined,
  l1: { bid: number | null; ask: number | null; last: number | null; lastLabel: string },
): BookHeader {
  if (!depth || depth.entitled !== true) {
    return { bid: l1.bid, ask: l1.ask, last: l1.last, lastLabel: l1.lastLabel };
  }

  let bid: number | null;
  let ask: number | null;
  if (depth.kind === "future") {
    bid = depth.bid[0]?.price ?? null;
    ask = depth.ask[0]?.price ?? null;
  } else if ((depth.kind === "option" || depth.kind === "combo") && depth.nbbo) {
    bid = depth.nbbo.bestBid ?? bestBidPrice(depth.bid);
    ask = depth.nbbo.bestAsk ?? bestAskPrice(depth.ask);
  } else if (depth.kind === "stock") {
    const uncrossed = uncrossStockBbo(depth, l1);
    bid = uncrossed.bid;
    ask = uncrossed.ask;
  } else {
    bid = bestBidPrice(depth.bid);
    ask = bestAskPrice(depth.ask);
  }

  if (isValidMarket(bid, ask)) {
    const mid =
      (depth.kind === "option" || depth.kind === "combo") && depth.nbbo?.mid != null
        ? depth.nbbo.mid
        : ((bid as number) + (ask as number)) / 2;
    return { bid, ask, last: mid, lastLabel: "MID" };
  }

  const last = l1.last != null && l1.last > 0 ? l1.last : null;
  return { bid, ask, last, lastLabel: last != null ? l1.lastLabel : "MID" };
}

/**
 * Running cumulative size from the inside out. Returned array is parallel to the
 * input: entry i is the sum of sizes 0..i.
 */
function runningCumulative(rows: DepthLevel[]): number[] {
  let sum = 0;
  return rows.map((row) => {
    sum += row.size;
    return sum;
  });
}
