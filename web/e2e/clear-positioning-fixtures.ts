import type { BreadthData, BreadthHistoryEntry, BreadthIntradayPoint } from "../lib/useBreadth";
import type { CotContract, CotPositioningData, CotWeek } from "../components/equibles-cot/cotPositioning";
import type { AtsVenueShareData, AtsVenueShareRow } from "../components/equibles-ats-venue-share/atsVenueShare";
import type { ShortCrowdingData, ShortCrowdingEntry } from "../components/equibles/shortCrowding";

// Source: tests/breadth-panel.test.tsx
export function BreadthFixture() {
  function buildHistory(count: number): BreadthHistoryEntry[] {
    return Array.from({ length: count }, (_, i) => ({
      date: `2026-06-${String((i % 28) + 1).padStart(2, "0")}`,
      net_ad: i % 2 === 0 ? 400 + i : -(200 + i),
      cum_ad: 48000 + i * 90,
      spy_close: 600 + i * 0.5,
    }));
  }

  function buildIntraday(): BreadthIntradayPoint[] {
    return [
      { time: "2026-07-01T13:35:00Z", net_ad: 120 },
      { time: "2026-07-01T13:40:00Z", net_ad: -60 },
      { time: "2026-07-01T13:45:00Z", net_ad: 240 },
      { time: "2026-07-01T13:50:00Z", net_ad: 310 },
    ];
  }

  function buildBreadthData(overrides: Partial<BreadthData> = {}): BreadthData {
    return {
      scan_time: "2026-07-01T19:55:00Z",
      market_open: true,
      source: "ib",
      latest: {
        session_date: "2026-07-01",
        net_ad: 820,
        net_ad_prev: -140,
        tick: 250,
        cum_ad: 51230,
        cum_ad_change_20d: 1900,
        spy_change_20d_pct: 2.4,
        sessions_positive_20: 13,
        divergence: "none",
      },
      history: buildHistory(30),
      intraday: buildIntraday(),
      ...overrides,
    };
  }
  return buildBreadthData();
}

// Source: tests/equibles-cot-positioning.test.tsx
export function CotFixture() {
  const WEEKS_IN_FIXTURE = 60;

  function latestReportTuesday(): Date {
    const now = new Date();
    const utcDay = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
    const weekday = new Date(utcDay).getUTCDay(); // Sun=0, Tue=2
    return new Date(utcDay - ((weekday - 2 + 7) % 7) * 86_400_000);
  }

  function week(index: number): CotWeek {
    const day = new Date(
      latestReportTuesday().getTime() - (WEEKS_IN_FIXTURE - 1 - index) * 7 * 86_400_000,
    );
    return {
      report_date: day.toISOString().slice(0, 10),
      net_noncommercial: 100_000 + index * 1_000,
      net_commercial: -(100_000 + index * 1_000),
      open_interest: 1_000_000,
      net_noncommercial_pct_oi: 10 + index * 0.1,
    };
  }

  function contract(alias: string, overrides: Partial<CotContract> = {}): CotContract {
    const series = Array.from({ length: WEEKS_IN_FIXTURE }, (_, i) => week(i));
    return {
      alias,
      market_code: `C${alias}`,
      name: `${alias} FUTURES`,
      category: "EquityIndices",
      report_date: series[series.length - 1].report_date,
      weeks: series.length,
      open_interest: 1_000_000,
      net_noncommercial: 159_000,
      net_commercial: -159_000,
      net_noncommercial_pct_oi: 15.9,
      net_noncommercial_change: 1_000,
      percentile: 98.3,
      z_score: 1.84,
      crowding: "CROWDED LONG",
      contrarian_bias: "BEARISH",
      series,
      ...overrides,
    };
  }

  function buildData(overrides: Partial<CotPositioningData> = {}): CotPositioningData {
    const contracts = [contract("ES"), contract("GC", { crowding: "NEUTRAL", contrarian_bias: "NEUTRAL", percentile: 44.1 })];
    return {
      scan_time: "2026-08-11T22:00:00Z",
      report_date: contracts[0].report_date,
      count: contracts.length,
      lookback_days: 1095,
      min_stats_weeks: 52,
      extremes: { high: 90, low: 10 },
      contracts,
      market: [
        {
          market_code: "CES",
          name: "ES FUTURES",
          category: "EquityIndices",
          report_date: contracts[0].report_date,
          open_interest: 1_000_000,
          net_commercial: -159_000,
          net_noncommercial: 159_000,
          net_noncommercial_pct_oi: 15.9,
        },
      ],
      ...overrides,
    };
  }
  return buildData();
}

// Source: tests/equibles-ats-venue-share.test.tsx
export function AtsFixture() {
  function row(overrides: Partial<AtsVenueShareRow> = {}): AtsVenueShareRow {
    return {
      ticker: "AAPL",
      week_start_date: "2026-08-03",
      ats_volume: 4_000_000,
      ats_trade_count: 8_000,
      non_ats_otc_volume: 6_000_000,
      non_ats_otc_trade_count: 30_000,
      total_off_exchange_volume: 10_000_000,
      ats_share_pct: 40,
      avg_ats_print_size: 500,
      avg_non_ats_print_size: 200,
      ats_share_z: 2.1,
      avg_ats_print_size_z: 1.8,
      short_volume_pct: 30,
      consolidated_volume: null,
      consolidated_volume_source: null,
      off_exchange_share_pct: null,
      classification: "accumulation",
      classification_reason: null,
      z_observations: 52,
      ...overrides,
    };
  }

  function data(overrides: Partial<AtsVenueShareData> = {}): AtsVenueShareData {
    const current = overrides.current ?? [row()];
    return {
      scan_time: "2026-08-11T12:00:00Z",
      source: "finra-off-exchange-weekly",
      count: current.length,
      tickers: current.map((r) => r.ticker ?? ""),
      week_start_date: "2026-08-03",
      thresholds: {
        accumulation_z: 1,
        distribution_z: -1,
        z_window_weeks: 52,
        min_history_weeks: 12,
      },
      series: {},
      errors: [],
      ...overrides,
      current,
    };
  }
  return data();
}

// Source: tests/equibles-short-crowding.test.tsx
export function ShortFixture() {
  function daysAgo(n: number): string {
    const d = new Date();
    d.setUTCDate(d.getUTCDate() - n);
    return d.toISOString().slice(0, 10);
  }

  const SETTLEMENT = daysAgo(9);

  function entry(overrides: Partial<ShortCrowdingEntry> = {}): ShortCrowdingEntry {
    return {
      ticker: "GME",
      settlement_date: SETTLEMENT,
      settlement_age_days: 9,
      prior_settlement_date: daysAgo(24),
      short_position: 55_000_000,
      prior_short_position: 50_000_000,
      change_in_short_position: 5_000_000,
      short_position_change_pct: 10,
      average_daily_volume: 8_600_000,
      days_to_cover: 6.4,
      crowding_tier: "extreme",
      squeeze_score: 88,
      squeeze_rank: 3,
      base_score: 80,
      catalyst_boost: 8,
      short_interest_pct: 22.5,
      short_interest_pct_basis: "shares",
      market_cap: 12_400_000_000,
      factors: null,
      readings: { upside_convexity: "extreme", short_leg_tail_risk: "extreme" },
      borrow: { source: "ib-short-availability", resolved: false },
      ...overrides,
    };
  }

  function payload(overrides: Partial<ShortCrowdingData> = {}): ShortCrowdingData {
    return {
      scan_time: new Date().toISOString(),
      count: 2,
      latest_settlement_date: SETTLEMENT,
      entries: [
        entry(),
        entry({
          ticker: "AAPL",
          squeeze_score: 24,
          squeeze_rank: 812,
          days_to_cover: 1.1,
          crowding_tier: "light",
          short_position: 120_000_000,
          short_position_change_pct: -4.2,
          short_interest_pct: 0.8,
          readings: { upside_convexity: "low", short_leg_tail_risk: "low" },
        }),
      ],
      board: [],
      ...overrides,
    };
  }
  return payload();
}
