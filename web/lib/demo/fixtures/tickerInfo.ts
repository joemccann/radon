import { demoBasePrice, demoSymbolHash, roundDemoPrice } from "./market";
import { marketDateKey, shiftDateKey } from "./time";

const DEMO_FUNDS = new Set(["SPY", "QQQ", "IWM", "DIA", "TLT", "HYG", "GLD", "XLF", "XLK", "XLE"]);
const DEMO_INDEXES = new Set(["SPX", "NDX", "RUT", "VIX", "VVIX", "COR1M"]);
const SECTORS = ["Technology", "Healthcare", "Financial Services", "Industrials", "Consumer Cyclical", "Energy"];

export type DemoTickerInfo = {
  source: "Sample data";
  uw_info: Record<string, unknown>;
  stock_state: Record<string, unknown>;
  profile: Record<string, unknown>;
  stats: Record<string, unknown>;
  short_float: Record<string, unknown>;
};

export type DemoSeasonalityMonth = {
  month: number;
  avg_change: number;
  median_change: number;
  max_change: number;
  min_change: number;
  positive_months_perc: number;
  years: number;
};

function round(value: number, places = 4): number {
  const scale = 10 ** places;
  return Math.round(value * scale) / scale;
}

function issueType(ticker: string): string {
  if (DEMO_INDEXES.has(ticker)) return "Index";
  if (DEMO_FUNDS.has(ticker)) return "ETF";
  return "Common Stock";
}

/** Request-time company sample for /api/ticker/info. No UW, Exa, Yahoo, or disk. */
export function buildDemoTickerInfo(rawTicker: string, now: Date = new Date()): DemoTickerInfo {
  const ticker = rawTicker.trim().toUpperCase();
  const seed = demoSymbolHash(ticker);
  const base = demoBasePrice(ticker);
  const prevClose = roundDemoPrice(base);
  const open = roundDemoPrice(base * (1 + ((seed % 9) - 4) / 1_000));
  const close = roundDemoPrice(base * (1 + ((seed % 13) - 6) / 800));
  const high = roundDemoPrice(Math.max(open, close) * 1.006);
  const low = roundDemoPrice(Math.min(open, close) * 0.994);
  const volume = 4_000_000 + (seed % 36_000_000);
  const sharesOutstanding = 300_000_000 + (seed % 9_700_000_000);
  const type = issueType(ticker);
  const isEquity = type === "Common Stock";

  return {
    source: "Sample data",
    uw_info: {
      name: `${ticker} Sample Holdings`,
      full_name: `${ticker} Sample Holdings`,
      issue_type: type,
      sector: isEquity ? SECTORS[seed % SECTORS.length] : "",
      description: `Sample company profile for ${ticker} on the Radon demo. Figures are generated for illustration and are not market data.`,
      marketcap: type === "Index" ? null : String(Math.round(sharesOutstanding * close)),
      beta: round(0.7 + (seed % 90) / 100, 2),
      avg30_volume: String(volume),
      next_earnings_date: isEquity ? shiftDateKey(marketDateKey(now), 14 + (seed % 60)) : null,
    },
    stock_state: {
      open,
      high,
      low,
      close,
      prev_close: prevClose,
      volume,
      tape_time: now.toISOString(),
    },
    profile: isEquity
      ? {
          ceo: "Sample CEO",
          employees: (2_000 + (seed % 180_000)).toLocaleString("en-US"),
          headquarters: "Sample City, USA",
          founded: String(1950 + (seed % 70)),
        }
      : {},
    stats: {
      pe_ratio: isEquity ? round(12 + (seed % 400) / 10, 2) : null,
      dividend_yield: type === "Index" ? null : round((seed % 300) / 100, 2),
      avg_volume: String(volume),
      week_52_high: roundDemoPrice(base * 1.24),
      week_52_low: roundDemoPrice(base * 0.71),
    },
    short_float: isEquity ? { symbol: ticker, total_float: Math.round(sharesOutstanding * 0.92) } : {},
  };
}

/** Twelve-month seasonality sample for /api/ticker/seasonality. No UW, Vision, or disk. */
export function buildDemoSeasonality(rawTicker: string): { source: "sample"; data: DemoSeasonalityMonth[] } {
  const ticker = rawTicker.trim().toUpperCase();
  const seed = demoSymbolHash(ticker);
  const data = Array.from({ length: 12 }, (_, index) => {
    const month = index + 1;
    const avg = round(0.012 * Math.sin((month + (seed % 12)) / 1.9) + 0.004);
    return {
      month,
      avg_change: avg,
      median_change: round(avg * 0.8),
      max_change: round(Math.abs(avg) + 0.06),
      min_change: round(-Math.abs(avg) - 0.05),
      positive_months_perc: round(0.5 + avg * 8, 2),
      years: 15,
    };
  });
  return { source: "sample", data };
}
