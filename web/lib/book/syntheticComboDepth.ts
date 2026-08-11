import type { DepthBook, DepthLevel } from "@/lib/pricesProtocol";

export const MAX_COMBO_BOOK_TIMESTAMP_SKEW_MS = 5_000;

export type SyntheticComboLeg = {
  direction: "LONG" | "SHORT";
  contracts: number;
  book: DepthBook | null | undefined;
};

export type SyntheticComboDepthBook = DepthBook & { kind: "combo" };

type NormalizedLeg = {
  direction: "LONG" | "SHORT";
  ratio: number;
  book: DepthBook;
};

type MutableLevel = DepthLevel & { remaining: number };

function gcd(a: number, b: number): number {
  while (b !== 0) [a, b] = [b, a % b];
  return a;
}

function venue(level: DepthLevel): string {
  return level.exchange ?? level.marketMaker ?? "UNKNOWN";
}

function validSide(levels: DepthLevel[]): boolean {
  return levels.length > 0 && levels.every((level) =>
    Number.isFinite(level.price) && Number.isFinite(level.size) && level.size > 0
  );
}

function isNoncrossed(book: DepthBook): boolean {
  return Math.max(...book.bid.map(({ price }) => price)) <= Math.min(...book.ask.map(({ price }) => price));
}

function aggregate(levels: DepthLevel[], side: "bid" | "ask"): DepthLevel[] {
  const byPrice = new Map<number, { size: number; venues: Set<string> }>();
  for (const level of levels) {
    const current = byPrice.get(level.price) ?? { size: 0, venues: new Set<string>() };
    current.size += level.size;
    if (level.exchange) current.venues.add(level.exchange);
    byPrice.set(level.price, current);
  }
  const sorted = [...byPrice.entries()].sort(([a], [b]) => side === "bid" ? b - a : a - b);
  return sorted.slice(0, 10).map(([price, value], index) => ({
    price,
    size: value.size,
    marketMaker: null,
    exchange: [...value.venues].sort().join(" | ") || null,
    nbbo: index === 0,
  }));
}

function normalizeLegs(legs: readonly SyntheticComboLeg[]): NormalizedLeg[] | null {
  if (legs.length < 2) return null;
  const bySymbol = new Map<string, { signedContracts: number; book: DepthBook }>();
  for (const leg of legs) {
    if (!Number.isInteger(leg.contracts) || leg.contracts <= 0 || !leg.book?.symbol.trim()) return null;
    const signedContracts = leg.direction === "LONG" ? leg.contracts : -leg.contracts;
    const current = bySymbol.get(leg.book.symbol);
    if (current) current.signedContracts += signedContracts;
    else bySymbol.set(leg.book.symbol, { signedContracts, book: leg.book });
  }
  const consolidated = [...bySymbol.values()].filter(({ signedContracts }) => signedContracts !== 0);
  if (consolidated.length < 2) return null;
  const divisor = consolidated.reduce((value, leg) => gcd(value, Math.abs(leg.signedContracts)), 0);
  return consolidated.map(({ signedContracts, book }) => ({
    direction: signedContracts > 0 ? "LONG" : "SHORT",
    ratio: Math.abs(signedContracts) / divisor,
    book,
  }));
}

function matchMarginal(legs: readonly NormalizedLeg[], side: "bid" | "ask"): DepthLevel[] {
  const queues = legs.map((leg) => {
    const useBid = side === "bid" ? leg.direction === "LONG" : leg.direction === "SHORT";
    const levels = useBid ? leg.book.bid : leg.book.ask;
    return [...levels]
      .sort((a, b) => useBid ? b.price - a.price : a.price - b.price)
      .map((level): MutableLevel => ({ ...level, remaining: Math.floor(level.size / leg.ratio) }))
      .filter((level) => level.remaining > 0);
  });
  const indexes = queues.map(() => 0);
  const output: DepthLevel[] = [];
  while (queues.every((queue, index) => indexes[index] < queue.length)) {
    const current = queues.map((queue, index) => queue[indexes[index]]);
    const size = Math.min(...current.map((level) => level.remaining));
    const price = legs.reduce((total, leg, index) => {
      const sign = leg.direction === "LONG" ? 1 : -1;
      return total + sign * leg.ratio * current[index].price;
    }, 0);
    const roundedPrice = Number(price.toFixed(10));
    output.push({
      price: Object.is(roundedPrice, -0) ? 0 : roundedPrice,
      size,
      marketMaker: null,
      exchange: current.map(venue).join(" / "),
    });
    current.forEach((level, index) => {
      level.remaining -= size;
      if (level.remaining <= Number.EPSILON) indexes[index] += 1;
    });
  }
  return output;
}

/** Builds indicative combo depth from executable, cross-sided component liquidity. */
export function buildSyntheticComboDepth(
  symbol: string,
  legs: readonly SyntheticComboLeg[],
): SyntheticComboDepthBook | null {
  if (!symbol.trim()) return null;
  const normalized = normalizeLegs(legs);
  if (!normalized) return null;
  if (normalized.some(({ book }) =>
    !book.entitled || (book.kind !== "option" && book.kind !== "stock") ||
    !validSide(book.bid) || !validSide(book.ask) || !isNoncrossed(book)
  )) return null;

  const timestamps = normalized.map(({ book }) => Date.parse(book.timestamp));
  if (timestamps.some((timestamp) => !Number.isFinite(timestamp))) return null;
  if (Math.max(...timestamps) - Math.min(...timestamps) > MAX_COMBO_BOOK_TIMESTAMP_SKEW_MS) return null;

  const bids = aggregate(matchMarginal(normalized, "bid"), "bid");
  const asks = aggregate(matchMarginal(normalized, "ask"), "ask");
  if (bids.length === 0 || asks.length === 0 || bids[0].price > asks[0].price) return null;

  const bestBid = bids[0].price;
  const bestAsk = asks[0].price;
  const roundedMid = Number(((bestBid + bestAsk) / 2).toFixed(10));
  return {
    symbol: symbol.trim(),
    kind: "combo",
    bid: bids,
    ask: asks,
    isSmartDepth: true,
    feed: "IMPLIED LEG BBO",
    entitled: true,
    nbbo: {
      bestBid,
      bestAsk,
      mid: Object.is(roundedMid, -0) ? 0 : roundedMid,
      bidSize: bids[0].size,
      askSize: asks[0].size,
    },
    timestamp: new Date(Math.max(...timestamps)).toISOString(),
  };
}
