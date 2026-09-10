const KNOWN_BASE_PRICES: Readonly<Record<string, number>> = {
  AAPL: 275,
  AAOI: 35,
  AMAT: 430,
  AMD: 175,
  COIN: 260,
  COR1M: 34,
  CRWD: 380,
  ES: 6_250,
  GOOG: 185,
  GOOGL: 185,
  IWM: 225,
  META: 620,
  MSFT: 450,
  MSTR: 370,
  NDX: 23_000,
  NEM: 85,
  NQ: 22_500,
  NVDA: 140,
  PLTR: 115,
  QQQ: 525,
  RTY: 2_450,
  RUT: 2_450,
  SPX: 6_250,
  SPY: 620,
  TSLA: 340,
  UBER: 85,
  VIX: 17.5,
  VVIX: 96,
};

export function demoSymbolHash(symbol: string): number {
  let hash = 2_166_136_261;
  for (const char of symbol.trim().toUpperCase()) {
    hash ^= char.charCodeAt(0);
    hash = Math.imul(hash, 16_777_619);
  }
  return hash >>> 0;
}

/** Stable sample anchor for arbitrary bounded tickers. */
export function demoBasePrice(symbol: string): number {
  const normalized = symbol.trim().toUpperCase();
  return KNOWN_BASE_PRICES[normalized] ?? 25 + (demoSymbolHash(normalized) % 35_000) / 100;
}

export function roundDemoPrice(value: number): number {
  return Math.round(value * 100) / 100;
}
