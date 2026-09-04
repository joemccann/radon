import type { ThetaHarvesterData, ThetaHarvesterResult } from "@/lib/types";
import { compactDate, marketDateKey, shiftDateKey } from "./time";

type BuildThetaOptions = {
  now?: Date;
  ticker?: string;
  preset?: string;
  limit?: number | null;
  minDte?: number | null;
  maxDte?: number | null;
  minCredit?: number | null;
};

const PRESET_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN"];

function hashTicker(ticker: string): number {
  return [...ticker].reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) % 10_007, 17);
}

function round(value: number, places = 2): number {
  const scale = 10 ** places;
  return Math.round(value * scale) / scale;
}

function fridayDte(now: Date, preferred: number, minDte: number, maxDte: number): number | null {
  const today = marketDateKey(now);
  const candidates: number[] = [];
  for (let dte = Math.max(1, minDte); dte <= maxDte; dte += 1) {
    const date = shiftDateKey(today, dte);
    if (new Date(`${date}T12:00:00.000Z`).getUTCDay() === 5) candidates.push(dte);
  }
  return candidates.sort((left, right) =>
    Math.abs(left - preferred) - Math.abs(right - preferred) || left - right,
  )[0] ?? null;
}

function buildCandidate(ticker: string, dte: number, now: Date): ThetaHarvesterResult {
  const seed = hashTicker(ticker);
  const spot = round(95 + (seed % 360) + (seed % 10) / 10);
  const putStrike = Math.max(5, Math.floor((spot * 0.92) / 5) * 5);
  const callStrike = Math.ceil((spot * 1.08) / 5) * 5;
  const expiry = compactDate(shiftDateKey(marketDateKey(now), dte));
  const iv = round(0.28 + (seed % 8) / 100, 3);
  const hv20 = round(iv - 0.075, 3);
  const credit = round(1.85 + (seed % 11) * 0.14);

  return {
    ticker,
    score: round(82 + (seed % 14) * 0.7, 1),
    verdict: "THETA_HARVEST",
    structure: {
      expiry,
      dte,
      short_put: {
        symbol: `${ticker}-${expiry}-${putStrike}P`,
        expiry,
        strike: putStrike,
        right: "P",
        iv,
        delta: -0.18,
        theta: -0.09,
        gamma: 0.004,
        vega: 0.11,
        bid: round(credit * 0.52),
        ask: round(credit * 0.56),
        volume: 420 + seed % 500,
        open_interest: 2_400 + seed % 2_000,
      },
      short_call: {
        symbol: `${ticker}-${expiry}-${callStrike}C`,
        expiry,
        strike: callStrike,
        right: "C",
        iv: round(iv - 0.01, 3),
        delta: 0.17,
        theta: -0.085,
        gamma: 0.0038,
        vega: 0.105,
        bid: round(credit * 0.48),
        ask: round(credit * 0.52),
        volume: 390 + seed % 450,
        open_interest: 2_100 + seed % 1_800,
      },
      net_delta: -0.01,
      theta: 0.175,
      gamma: -0.0078,
      vega: -0.215,
      credit,
    },
    spot,
    iv,
    hv20,
    hv60: round(iv - 0.055, 3),
    iv_rv_edge: 7.5,
    iv_rv_ratio: round(iv / hv20, 2),
    trend_20d_pct: round(((seed % 9) - 4) * 0.34),
    range_score: round(0.79 + (seed % 8) / 100, 2),
    dealer_support: "SUPPORT",
    net_gex: 1_250_000 + seed * 100,
    gex_flip: round(spot * 0.96),
    setup: "Range-bound sample with rich implied volatility and dealer support.",
    gates: {
      delta_near_zero: true,
      iv_rich_vs_rv: true,
      dealer_support: true,
      theta_positive: true,
      range_bound: true,
    },
    errors: [],
    earnings: null,
  };
}

/** Complete scan data for the default universe or one validated ticker. */
export function buildDemoThetaHarvester(options: BuildThetaOptions = {}): ThetaHarvesterData {
  const now = options.now ?? new Date();
  const ticker = options.ticker?.trim().toUpperCase() ?? "";
  const minDte = options.minDte ?? 7;
  const maxDte = options.maxDte ?? 45;
  const requested = ticker ? [ticker] : PRESET_TICKERS;
  const preferredDtes = ticker
    ? [Math.max(minDte, Math.min(maxDte, 30))]
    : [21, 28, 35, 42];
  let results = minDte > maxDte
    ? []
    : requested.flatMap((symbol, index) => {
        const preferred = preferredDtes[index] ?? preferredDtes[0];
        const dte = fridayDte(now, preferred, minDte, maxDte);
        return dte == null ? [] : [buildCandidate(symbol, dte, now)];
      });
  results = results.filter((result) =>
    result.structure.dte >= minDte
    && result.structure.dte <= maxDte
    && (result.structure.credit ?? 0) >= (options.minCredit ?? 0),
  );
  if (!ticker && options.limit != null && Number.isFinite(options.limit) && options.limit > 0) {
    results = results.slice(0, Math.trunc(options.limit));
  }

  return {
    scan_time: now.toISOString(),
    source: "Sample data",
    universe: ticker ? "explicit" : `preset:${options.preset ?? "ndx100"}`,
    requested_tickers: requested,
    tickers_scanned: requested.length,
    candidates_found: results.length,
    theta_harvest_count: results.filter((result) => result.verdict === "THETA_HARVEST").length,
    results,
  };
}
