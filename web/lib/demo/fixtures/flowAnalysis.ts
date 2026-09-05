import type { FlowReportData } from "@/lib/useTickerFlowReport";
import { businessDateKeys } from "./time";

function hashTicker(ticker: string): number {
  return [...ticker].reduce((hash, char) => (hash * 37 + char.charCodeAt(0)) % 100_003, 23);
}

function round(value: number, places = 3): number {
  const scale = 10 ** places;
  return Math.round(value * scale) / scale;
}

/** Fresh deterministic market-structure sample for any route-valid ticker. */
export function buildDemoFlowReport(rawTicker: string, now: Date = new Date()): FlowReportData {
  const ticker = rawTicker.trim().toUpperCase();
  const seed = hashTicker(ticker);
  const bucket = seed % 3;
  const direction = bucket === 0 ? "BULLISH" : bucket === 1 ? "BEARISH" : "NEUTRAL";
  const buyRatio = direction === "BULLISH"
    ? 0.61 + (seed % 7) / 100
    : direction === "BEARISH"
      ? 0.33 + (seed % 7) / 100
      : 0.48 + (seed % 5) / 100;
  const confidence = direction === "NEUTRAL" ? 58 + seed % 12 : 72 + seed % 17;
  const dates = businessDateKeys(12, now);
  const totalVolume = 1_800_000 + (seed % 900_000);
  const totalPremium = 28_000_000 + (seed % 16_000_000);
  const numPrints = 135 + seed % 90;
  const callPremium = direction === "BEARISH" ? 9_800_000 : 17_600_000;
  const putPremium = direction === "BULLISH" ? 8_200_000 : 15_900_000;

  return {
    ticker,
    fetched_at: now.toISOString(),
    lookback_days: 20,
    verdict: { direction, confidence },
    analysis: {
      signal: `${direction}_FLOW`,
      score: round((confidence / 100) * (direction === "BEARISH" ? -1 : direction === "NEUTRAL" ? 0.12 : 1)),
      direction,
      strength: round(confidence / 100),
      buy_ratio: round(buyRatio),
      sustained_days: direction === "NEUTRAL" ? 3 : 8,
      num_prints: numPrints,
      options_conflict: false,
    },
    dark_pool: {
      aggregate: {
        flow_direction: direction,
        flow_strength: round(confidence / 100),
        dp_buy_ratio: round(buyRatio),
        total_volume: totalVolume,
        total_premium: totalPremium,
        buy_volume: Math.round(totalVolume * buyRatio),
        sell_volume: Math.round(totalVolume * (1 - buyRatio)),
        num_prints: numPrints,
      },
      daily: dates.map((date, index) => {
        const dailyRatio = Math.max(0.05, Math.min(0.95, buyRatio + Math.sin((seed + index) * 0.7) * 0.045));
        return {
          date,
          flow_direction: dailyRatio > 0.55 ? "BULLISH" : dailyRatio < 0.45 ? "BEARISH" : "NEUTRAL",
          flow_strength: round(Math.abs(dailyRatio - 0.5) * 2),
          dp_buy_ratio: round(dailyRatio),
          num_prints: 8 + (seed + index * 7) % 19,
        };
      }),
    },
    options_flow: {
      bias: direction,
      put_call_ratio: round(putPremium / callPremium),
      call_premium: callPremium,
      put_premium: putPremium,
      total_alerts: 28 + seed % 24,
    },
    combined_signal: `${direction} / ${confidence}% confidence`,
    market_status: "Sample data",
    trading_days_checked: dates,
    cache_meta: {
      last_refresh: now.toISOString(),
      age_seconds: 0,
      is_stale: false,
    },
  };
}
