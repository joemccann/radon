import { isFuturesRoot } from "@/lib/futuresSymbols";
import {
  optionKey,
  parseOptionKey,
  type DepthBook,
  type FundamentalsData,
  type IndexContract,
  type OptionContract,
  type PriceData,
  type Trade,
} from "@/lib/pricesProtocol";
import { demoBasePrice, demoSymbolHash, roundDemoPrice } from "./fixtures/market";

export type DemoRealtimeRequest = {
  symbols: string[];
  contracts: OptionContract[];
  indexes: IndexContract[];
  depthSymbol: string | null;
  depthSymbols: string[];
  depthExpiry: string | null;
};

export type DemoRealtimeSample = {
  prices: Record<string, PriceData>;
  fundamentals: Record<string, FundamentalsData>;
  depths: Record<string, DepthBook>;
  tape: Record<string, Trade[]>;
};

function sampleMinute(now: Date): Date {
  return new Date(Math.floor(now.getTime() / 60_000) * 60_000);
}

function minuteSeed(symbol: string, now: Date): number {
  const minute = Math.floor(now.getTime() / 60_000);
  return demoSymbolHash(`${symbol}:${minute}`);
}

function instrumentTick(symbol: string, price: number): number {
  if (isFuturesRoot(symbol)) return symbol === "RTY" ? 0.1 : 0.25;
  if (price >= 1_000) return 0.25;
  return 0.01;
}

function liveUnderlyingPrice(symbol: string, now: Date): number {
  const base = demoBasePrice(symbol);
  const drift = ((minuteSeed(symbol, now) % 101) - 50) / 20_000;
  return roundDemoPrice(base * (1 + drift));
}

function buildUnderlyingQuote(symbol: string, now: Date): PriceData {
  const normalized = symbol.trim().toUpperCase();
  const last = liveUnderlyingPrice(normalized, now);
  const tick = instrumentTick(normalized, last);
  const bid = roundDemoPrice(last - tick);
  const ask = roundDemoPrice(last + tick);
  const closeDirection = demoSymbolHash(normalized) % 2 === 0 ? 1 : -1;
  const close = roundDemoPrice(demoBasePrice(normalized) * (1 - closeDirection * 0.004));
  const fwd = normalized === "VIX" ? roundDemoPrice(last + 0.85) : null;

  return {
    symbol: normalized,
    last,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 60 + (demoSymbolHash(`${normalized}:bid`) % 240),
    askSize: 55 + (demoSymbolHash(`${normalized}:ask`) % 220),
    volume: 250_000 + (demoSymbolHash(`${normalized}:volume`) % 4_000_000),
    high: roundDemoPrice(Math.max(last, close) * 1.008),
    low: roundDemoPrice(Math.min(last, close) * 0.992),
    open: roundDemoPrice((last + close) / 2),
    close,
    week52High: roundDemoPrice(Math.max(last, close) * 1.24),
    week52Low: roundDemoPrice(Math.min(last, close) * 0.72),
    avgVolume: 1_500_000 + (demoSymbolHash(`${normalized}:average`) % 8_000_000),
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    ...(fwd == null ? {} : { fwd, fwdCurve: {} }),
    timestamp: now.toISOString(),
  };
}

function daysToExpiry(expiry: string, now: Date): number {
  const iso = `${expiry.slice(0, 4)}-${expiry.slice(4, 6)}-${expiry.slice(6, 8)}T20:00:00.000Z`;
  return Math.max(1, (Date.parse(iso) - now.getTime()) / 86_400_000);
}

function buildOptionQuote(
  contract: OptionContract,
  underlying: PriceData,
  now: Date,
): PriceData {
  const key = optionKey(contract);
  const spot = underlying.last ?? demoBasePrice(contract.symbol);
  const intrinsic = contract.right === "C"
    ? Math.max(0, spot - contract.strike)
    : Math.max(0, contract.strike - spot);
  const dte = daysToExpiry(contract.expiry, now);
  const timeValue = Math.max(0.2, spot * 0.018 * Math.sqrt(dte / 30));
  const distanceDecay = Math.exp(-Math.abs(contract.strike - spot) / Math.max(spot * 0.16, 1));
  const last = roundDemoPrice(Math.max(0.05, intrinsic + timeValue * distanceDecay));
  const halfSpread = Math.max(0.01, roundDemoPrice(last * 0.025));
  const bid = roundDemoPrice(Math.max(0.01, last - halfSpread));
  const ask = roundDemoPrice(last + halfSpread);
  const callDelta = Math.max(0.08, Math.min(0.92, 0.5 + (spot - contract.strike) / Math.max(spot * 0.45, 1)));
  const delta = contract.right === "C" ? callDelta : callDelta - 1;

  return {
    symbol: key,
    last,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 10 + (demoSymbolHash(`${key}:bid`) % 90),
    askSize: 10 + (demoSymbolHash(`${key}:ask`) % 90),
    volume: 50 + (demoSymbolHash(`${key}:volume`) % 2_500),
    high: roundDemoPrice(last * 1.12),
    low: roundDemoPrice(Math.max(0.01, last * 0.88)),
    open: roundDemoPrice(last * 0.98),
    close: roundDemoPrice(last * 0.97),
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: Math.round(delta * 10_000) / 10_000,
    gamma: Math.round((0.01 + distanceDecay * 0.03) * 10_000) / 10_000,
    theta: -roundDemoPrice(Math.max(0.01, timeValue / Math.max(dte, 1))),
    vega: roundDemoPrice(spot * 0.0015 * distanceDecay),
    impliedVol: Math.round((0.22 + (demoSymbolHash(key) % 1_500) / 10_000) * 10_000) / 10_000,
    undPrice: spot,
    timestamp: now.toISOString(),
  };
}

function buildFundamentals(symbol: string, quote: PriceData, now: Date): FundamentalsData {
  const seed = demoSymbolHash(symbol);
  return {
    symbol,
    peRatio: Math.round((12 + (seed % 2_800) / 100) * 100) / 100,
    eps: Math.round((1 + (seed % 1_200) / 100) * 100) / 100,
    dividendYield: Math.round((seed % 320) / 100) / 100,
    week52High: quote.week52High,
    week52Low: quote.week52Low,
    priceBookRatio: Math.round((1 + (seed % 900) / 100) * 100) / 100,
    roe: Math.round((8 + (seed % 2_200) / 100) * 100) / 100,
    revenue: 1_000_000_000 + (seed % 80_000) * 1_000_000,
    timestamp: now.toISOString(),
  };
}

function depthKind(subject: string): DepthBook["kind"] {
  if (parseOptionKey(subject)) return "option";
  if (isFuturesRoot(subject)) return "future";
  return "stock";
}

function buildDepth(subject: string, quote: PriceData, now: Date): DepthBook {
  const kind = depthKind(subject);
  const bid = quote.bid ?? quote.last ?? 0;
  const ask = quote.ask ?? quote.last ?? 0;
  const tick = kind === "option" ? 0.01 : instrumentTick(subject, quote.last ?? 0);
  const exchanges = ["CBOE", "PHLX", "ISE", "ARCA", "EDGX"];
  const bidLevels = Array.from({ length: 5 }, (_, index) => ({
    price: roundDemoPrice(Math.max(tick, bid - index * tick)),
    size: 20 + (demoSymbolHash(`${subject}:bid:${index}`) % 180),
    marketMaker: kind === "stock" ? `D${index + 1}` : null,
    exchange: kind === "future" ? null : exchanges[index],
    ...(kind === "option" ? { nbbo: index === 0 } : {}),
  }));
  const askLevels = Array.from({ length: 5 }, (_, index) => ({
    price: roundDemoPrice(ask + index * tick),
    size: 20 + (demoSymbolHash(`${subject}:ask:${index}`) % 180),
    marketMaker: kind === "stock" ? `D${index + 6}` : null,
    exchange: kind === "future" ? null : exchanges[(index + 2) % exchanges.length],
    ...(kind === "option" ? { nbbo: index === 0 } : {}),
  }));

  return {
    symbol: subject,
    kind,
    bid: bidLevels,
    ask: askLevels,
    isSmartDepth: kind !== "future",
    feed: kind === "option"
      ? "SAMPLE OPRA · PER-EXCHANGE BBO"
      : kind === "future"
        ? "SAMPLE CME · GLOBEX DEPTH"
        : "SAMPLE SMART DEPTH",
    entitled: true,
    ...(kind === "option" ? {
      nbbo: {
        bestBid: bid,
        bestAsk: ask,
        mid: roundDemoPrice((bid + ask) / 2),
        bidSize: quote.bidSize ?? 0,
        askSize: quote.askSize ?? 0,
      },
    } : {}),
    timestamp: now.toISOString(),
  };
}

function buildTape(subject: string, quote: PriceData, now: Date): Trade[] {
  const last = quote.last ?? 0;
  const tick = depthKind(subject) === "option" ? 0.01 : instrumentTick(subject, last);
  return Array.from({ length: 8 }, (_, index) => {
    const direction = index % 3 === 0 ? -1 : index % 3 === 1 ? 0 : 1;
    return {
      price: roundDemoPrice(Math.max(tick, last + direction * tick)),
      size: 1 + (demoSymbolHash(`${subject}:trade:${index}`) % 75),
      exchange: depthKind(subject) === "future" ? null : index % 2 === 0 ? "ARCA" : "FINY",
      time: String(Math.floor(now.getTime() / 1_000) - (7 - index) * 3),
    };
  });
}

export function buildDemoSnapshotPrices(
  symbols: string[],
  now: Date = new Date(),
): Record<string, PriceData> {
  const sampledAt = sampleMinute(now);
  const prices: Record<string, PriceData> = {};
  for (const rawSymbol of symbols) {
    const symbol = rawSymbol.trim().toUpperCase();
    if (!symbol || prices[symbol]) continue;
    prices[symbol] = buildUnderlyingQuote(symbol, sampledAt);
  }
  return prices;
}

export function buildDemoRealtimeSample(
  request: DemoRealtimeRequest,
  now: Date = new Date(),
): DemoRealtimeSample {
  const sampledAt = sampleMinute(now);
  const indexSymbols = request.indexes.map(({ symbol }) => symbol);
  const contractSymbols = request.contracts.map(({ symbol }) => symbol);
  const prices = buildDemoSnapshotPrices(
    [...request.symbols, ...indexSymbols, ...contractSymbols],
    sampledAt,
  );
  const fundamentals: Record<string, FundamentalsData> = {};
  for (const symbol of [...new Set([...request.symbols, ...contractSymbols])]) {
    const normalized = symbol.trim().toUpperCase();
    const quote = prices[normalized];
    if (quote && !isFuturesRoot(normalized)) {
      fundamentals[normalized] = buildFundamentals(normalized, quote, sampledAt);
    }
  }

  for (const contract of request.contracts) {
    const normalizedSymbol = contract.symbol.trim().toUpperCase();
    const underlying = prices[normalizedSymbol] ?? buildUnderlyingQuote(normalizedSymbol, sampledAt);
    prices[normalizedSymbol] = underlying;
    prices[optionKey(contract)] = buildOptionQuote(contract, underlying, sampledAt);
  }

  const requestedDepth = request.depthSymbols.length > 0
    ? request.depthSymbols
    : request.depthSymbol
      ? [request.depthSymbol]
      : [];
  const depthSubjects = [...new Set(requestedDepth.map((subject) => subject.trim()).filter(Boolean))].slice(0, 3);
  const depths: Record<string, DepthBook> = {};
  const tape: Record<string, Trade[]> = {};
  for (const subject of depthSubjects) {
    const parsed = parseOptionKey(subject);
    let quote = prices[subject];
    if (!quote && parsed) {
      const underlying = prices[parsed.symbol]
        ?? buildUnderlyingQuote(parsed.symbol, sampledAt);
      prices[parsed.symbol] = underlying;
      quote = buildOptionQuote(parsed, underlying, sampledAt);
    }
    quote ??= buildUnderlyingQuote(subject, sampledAt);
    prices[subject] = quote;
    depths[subject] = buildDepth(subject, quote, sampledAt);
    tape[subject] = buildTape(subject, quote, sampledAt);
  }

  return { prices, fundamentals, depths, tape };
}
