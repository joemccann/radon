import type { BpiIndexSymbol, BpiPayload, BpiResponse, BpiState } from "@/lib/bpi";
import type { DispersionData, DispersionPoint } from "@/lib/dispersion";
import { lastCompletedSessionDate, mostRecentSessionDate } from "@/lib/marketSession";
import type { TrinData, TrinHourlyBar, TrinState } from "@/lib/trin";
import type { GammaRotationData, GammaRotationHistoryEntry } from "@/lib/useGammaRotation";
import type { GexBucket, GexData, GexHistoryEntry } from "@/lib/useGex";
import type { CriData, CriHistoryEntry } from "@/lib/useRegime";
import type { VcgData, VcgHistoryEntry } from "@/lib/useVcg";

const ET_TIME_ZONE = "America/New_York";

function round(value: number, decimals = 2): number {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

function etCalendarDate(now: Date): string {
  return now.toLocaleDateString("sv", { timeZone: ET_TIME_ZONE });
}

function isMarketOpenAt(now: Date): boolean {
  const parts = new Intl.DateTimeFormat("en-US-u-hc-h23", {
    timeZone: ET_TIME_ZONE,
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  if (values.weekday === "Sat" || values.weekday === "Sun") return false;
  const minutes = Number(values.hour) * 60 + Number(values.minute);
  return minutes >= 9 * 60 + 30 && minutes <= 16 * 60;
}

function etWallClockTimestamp(sessionDate: string, hour: number, minute: number): string {
  const probe = new Date(`${sessionDate}T16:00:00.000Z`);
  const zoneName = new Intl.DateTimeFormat("en-US", {
    timeZone: ET_TIME_ZONE,
    timeZoneName: "longOffset",
  }).formatToParts(probe).find((part) => part.type === "timeZoneName")?.value;
  const offset = zoneName?.match(/^GMT([+-]\d{2}:\d{2})$/)?.[1] ?? "-05:00";
  return new Date(
    `${sessionDate}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00${offset}`,
  ).toISOString();
}

/**
 * Use request time while it belongs to the expected ET session. Overnight and
 * weekend requests use that session's close so freshness checks do not mistake
 * the calendar date for a missing trading session.
 */
function currentSessionTimestamp(now: Date, sessionDate = mostRecentSessionDate(now)): string {
  return etCalendarDate(now) === sessionDate
    ? now.toISOString()
    : etWallClockTimestamp(sessionDate, 16, 0);
}

function sessionDatesEndingAt(count: number, endDate: string): string[] {
  const dates: string[] = [];
  const cursor = new Date(`${endDate}T12:00:00.000Z`);
  while (dates.length < count) {
    const day = cursor.getUTCDay();
    if (day !== 0 && day !== 6) dates.unshift(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() - 1);
  }
  return dates;
}

export function buildDemoCriFixture(now: Date = new Date()): CriData {
  const date = mostRecentSessionDate(now);
  const scanTime = currentSessionTimestamp(now, date);
  const marketOpen = isMarketOpenAt(now);
  const dates = sessionDatesEndingAt(42, date);
  const history: CriHistoryEntry[] = dates.map((session, index) => ({
    date: session,
    vix: round(17.2 + 1.8 * Math.sin(index / 4)),
    vvix: round(94 + 8 * Math.cos(index / 5)),
    spy: round(621 + index * 0.52 + 2.5 * Math.sin(index / 6)),
    cor1m: round(38 + 7 * Math.sin(index / 7)),
    realized_vol: round(13.5 + 1.4 * Math.cos(index / 8)),
    spx_vs_ma_pct: round(1.2 + 0.9 * Math.sin(index / 9)),
    vix_5d_roc: round(-2.5 + 4 * Math.cos(index / 6)),
  }));
  history[history.length - 1] = {
    date,
    vix: 18.64,
    vvix: 97.18,
    spy: 642.35,
    cor1m: 42.6,
    realized_vol: 14.2,
    spx_vs_ma_pct: 1.17,
    vix_5d_roc: -3.4,
  };

  return {
    scan_time: scanTime,
    market_open: marketOpen,
    date,
    vix: 18.64,
    vvix: 97.18,
    spy: 642.35,
    vix_5d_roc: -3.4,
    vvix_vix_ratio: 5.21,
    spx_100d_ma: 634.92,
    spx_distance_pct: 1.17,
    cor1m: 42.6,
    cor1m_previous_close: 41.9,
    cor1m_5d_change: 2.1,
    realized_vol: 14.2,
    cri: {
      score: 31,
      level: "ELEVATED",
      components: { vix: 8, vvix: 9, correlation: 8, momentum: 6 },
    },
    cta: {
      realized_vol: 14.2,
      exposure_pct: 126,
      forced_reduction_pct: 0,
      est_selling_bn: 0,
    },
    menthorq_cta: null,
    crash_trigger: {
      triggered: false,
      conditions: {
        spx_below_100d_ma: false,
        realized_vol_gt_25: false,
        cor1m_gt_60: false,
      },
      values: { spx_distance_pct: 1.17, realized_vol: 14.2, cor1m: 42.6 },
    },
    history,
    spy_closes: history.slice(-21).map((entry) => entry.spy),
  };
}

export function buildDemoVcgFixture(now: Date = new Date()): VcgData {
  const date = mostRecentSessionDate(now);
  const dates = sessionDatesEndingAt(42, date);
  const history: VcgHistoryEntry[] = dates.map((session, index) => ({
    date: session,
    residual: round(-0.004 + 0.00018 * index + 0.001 * Math.sin(index / 4), 4),
    vcg: round(-0.8 + index * 0.035 + 0.25 * Math.sin(index / 5)),
    vcg_adj: round(-0.7 + index * 0.032 + 0.22 * Math.sin(index / 5)),
    beta1: -0.0139,
    beta2: -0.023,
    vix: round(17.5 + 2 * Math.sin(index / 5)),
    vvix: round(95 + 9 * Math.cos(index / 6)),
    credit: round(79.2 + 0.06 * index + 0.3 * Math.cos(index / 7)),
  }));
  history[history.length - 1] = {
    date,
    residual: 0.0027,
    vcg: 0.74,
    vcg_adj: 0.61,
    beta1: -0.0139,
    beta2: -0.023,
    vix: 18.64,
    vvix: 97.18,
    credit: 81.73,
  };

  return {
    scan_time: currentSessionTimestamp(now, date),
    market_open: isMarketOpenAt(now),
    credit_proxy: "HYG",
    signal: {
      vcg: 0.74,
      vcg_adj: 0.61,
      residual: 0.0027,
      beta1_vvix: -0.0139,
      beta2_vix: -0.023,
      alpha: 0,
      vix: 18.64,
      vvix: 97.18,
      credit_price: 81.73,
      credit_5d_return_pct: 0.32,
      ro: 0,
      edr: 0,
      tier: null,
      bounce: 0,
      vvix_severity: "moderate",
      sign_ok: true,
      sign_suppressed: false,
      pi_panic: 0.08,
      regime: "DIVERGENCE",
      interpretation: "WATCH",
      attribution: {
        vvix_pct: 54,
        vix_pct: 46,
        vvix_component: 0.41,
        vix_component: 0.33,
        model_implied: 0.74,
      },
    },
    history,
  };
}

export function buildDemoGammaRotationFixture(now: Date = new Date()): GammaRotationData {
  const date = mostRecentSessionDate(now);
  const history: GammaRotationHistoryEntry[] = sessionDatesEndingAt(63, date).map((session, index) => {
    const spyZ = round(0.35 + 1.1 * Math.sin(index / 8));
    const tltZ = round(-0.2 + 0.9 * Math.cos(index / 9));
    const spyNetGamma = round(160_000 + 420_000 * Math.sin(index / 7));
    const tltNetGamma = round(-100_000 + 500_000 * Math.cos(index / 8));
    const rawSpread = round(spyZ - tltZ, 3);
    const state = spyNetGamma > 0 && tltNetGamma < 0
      ? "RISK_ON_DIVERGENCE"
      : spyNetGamma < 0 && tltNetGamma > 0
        ? "RISK_OFF_DIVERGENCE"
        : spyNetGamma > 0 && tltNetGamma > 0
          ? "DUAL_CUSHION"
          : "DUAL_WHIP";
    return {
      date: session,
      spy_net_gamma: spyNetGamma,
      tlt_net_gamma: tltNetGamma,
      spy_gamma_z: spyZ,
      tlt_gamma_z: tltZ,
      grg_z: round(rawSpread * 0.72 + 0.18 * Math.sin(index / 5)),
      raw_spread: rawSpread,
      state,
    };
  });
  history[history.length - 4].spy_net_gamma = -566_900;
  history[history.length - 4].tlt_net_gamma = 2_508_000;
  history[history.length - 4].state = "RISK_OFF_DIVERGENCE";
  history[history.length - 1] = {
    date,
    spy_net_gamma: -382_900,
    tlt_net_gamma: 2_600_000,
    spy_gamma_z: -0.38,
    tlt_gamma_z: 0.32,
    grg_z: -0.7,
    raw_spread: -0.7,
    state: "RISK_OFF_DIVERGENCE",
  };

  return {
    scan_time: currentSessionTimestamp(now, date),
    market_open: isMarketOpenAt(now),
    data_date: date,
    source: "Sample snapshot",
    storage: "demo",
    lookback_days: 250,
    z_window: 63,
    signal: {
      state: "RISK_OFF_DIVERGENCE",
      state_label: "Risk-off divergence",
      interpretation: "RISK_OFF",
      tier: 3,
      top_watch: false,
      bottom_watch: true,
      top_score: 1,
      bottom_score: 4,
      grg_z: -0.7,
      raw_spread: -0.7,
      spy_gamma_z: -0.38,
      tlt_gamma_z: 0.32,
      spy_3d_gamma_change: 184_000,
      tlt_3d_gamma_change: 92_000,
      summary: "SPY gamma is amplifying equity moves while TLT gamma is cushioning duration.",
    },
    assets: {
      SPY: {
        ticker: "SPY",
        spot: 642.35,
        data_date: date,
        strike_data_date: date,
        net_gamma: -382_900,
        net_gex: -382_900,
        call_gex: 2_461_000,
        put_gex: -2_843_900,
        net_delta: 84_200_000,
        gamma_z: -0.38,
        gamma_1d_change: -71_000,
        gamma_3d_change: 184_000,
        state: "WHIP",
        spot_vs_flip_pct: 0.2,
        levels: {
          gex_flip: { strike: 641, gamma: 0, distance: -1.35, distance_pct: -0.21 },
        },
      },
      TLT: {
        ticker: "TLT",
        spot: 91.48,
        data_date: date,
        strike_data_date: date,
        net_gamma: 2_600_000,
        net_gex: 2_600_000,
        call_gex: 3_170_000,
        put_gex: -570_000,
        net_delta: 19_400_000,
        gamma_z: 0.32,
        gamma_1d_change: 31_000,
        gamma_3d_change: 92_000,
        state: "CUSHION",
        spot_vs_flip_pct: 0.52,
        levels: {
          gex_flip: { strike: 91, gamma: 0, distance: -0.48, distance_pct: -0.52 },
        },
      },
    },
    gates: [
      { id: "polarity", label: "Polarity", status: "WATCH", copy: "SPY positive and TLT negative identifies the clean risk-on divergence." },
      { id: "magnitude", label: "Magnitude", status: "WATCH", copy: "Absolute GRG above 2σ means the cross-asset gamma spread is statistically stretched." },
      { id: "spy_cushion", label: "SPY cushion", status: "FAIL", copy: "Positive SPY gamma means dealer hedging is mechanically dampening equity moves." },
      { id: "duration_whip", label: "TLT whip", status: "WATCH", copy: "Negative TLT gamma means duration moves are mechanically amplified." },
      { id: "decay", label: "Decay", status: "WATCH", copy: "A negative 3-session SPY gamma slope marks possible equity cushion decay." },
      { id: "flip", label: "Flip", status: "PASS", copy: "Spot above the SPY gamma flip keeps the equity cushion valid." },
    ],
    history,
    top_bottom: {
      top: { active: false, copy: "Top gate is inactive." },
      bottom: { active: true, copy: "Four of five bottom gates are active." },
    },
  };
}

function gexLevel(strike: number, gamma: number, spot: number) {
  const distance = round(strike - spot);
  return { strike, gamma, distance, distance_pct: round((distance / spot) * 100) };
}

export function buildDemoGexFixture(now: Date = new Date()): GexData {
  const date = mostRecentSessionDate(now);
  const spot = 642.35;
  const strikes = [620, 625, 630, 635, 640, 645, 650, 655, 660];
  const profile: GexBucket[] = strikes.map((strike, index) => {
    const callGex = round(1_100 + index * 310 + 420 * Math.sin(index / 2));
    const putGex = round(-(3_600 - index * 330 + 280 * Math.cos(index / 2)));
    return {
      strike,
      call_gex: callGex,
      put_gex: putGex,
      net_gex: round(callGex + putGex),
      pct_from_spot: round(((strike - spot) / spot) * 100),
      tag: strike === 640 ? "GEX FLIP" : strike === 660 ? "MAX MAGNET" : null,
    };
  });
  const history: GexHistoryEntry[] = sessionDatesEndingAt(30, date).map((session, index) => ({
    date: session,
    net_gex: round(-155_000 + index * 4_100 + 22_000 * Math.sin(index / 4)),
    net_dex: round(28_000 + 8_000 * Math.cos(index / 5)),
    gex_flip: round(626 + index * 0.4),
    spot: round(626 + index * 0.55 + 2 * Math.sin(index / 5)),
    atm_iv: round(21 - index * 0.08 + Math.sin(index / 4)),
    vol_pc: round(1.05 + 0.2 * Math.cos(index / 5)),
    bias: index > 18 ? "CAUTIOUS_BULL" : "NEUTRAL",
  }));
  history[history.length - 1] = {
    date,
    net_gex: -82_400,
    net_dex: 34_700,
    gex_flip: 640,
    spot,
    atm_iv: 18.9,
    vol_pc: 1.18,
    bias: "CAUTIOUS_BULL",
  };

  return {
    scan_time: currentSessionTimestamp(now, date),
    market_open: isMarketOpenAt(now),
    ticker: "SPX",
    spot,
    close: 640.98,
    day_change: 1.37,
    day_change_pct: 0.21,
    data_date: date,
    net_gex: -82_400,
    net_dex: 34_700,
    atm_iv: 18.9,
    vol_pc: 1.18,
    levels: {
      gex_flip: gexLevel(640, 0, spot),
      max_magnet: gexLevel(660, profile.find((bucket) => bucket.strike === 660)?.net_gex ?? 0, spot),
      second_magnet: gexLevel(655, profile.find((bucket) => bucket.strike === 655)?.net_gex ?? 0, spot),
      max_accelerator: gexLevel(620, profile.find((bucket) => bucket.strike === 620)?.net_gex ?? 0, spot),
      put_wall: gexLevel(620, profile.find((bucket) => bucket.strike === 620)?.net_gex ?? 0, spot),
      call_wall: gexLevel(660, profile.find((bucket) => bucket.strike === 660)?.net_gex ?? 0, spot),
    },
    profile,
    expected_range: { low: 632, high: 653, iv_1d: 1.63 },
    bias: {
      direction: "CAUTIOUS_BULL",
      reasons: ["Spot is above the gamma flip", "Net gamma remains negative", "Primary magnet is above spot"],
      days_above_flip: 3,
      flip_migration: history.slice(-5).map((entry) => ({ date: entry.date, flip: entry.gex_flip ?? 635 })),
    },
    history,
    iv: { iv30d: 18.9, iv_rank: 36, hv30: 16.7, mq_iv30d: 19.1, mq_iv_rank: "38%", source: "both" },
    mq: {
      source_date: date,
      spot,
      hvl: 635,
      call_resistance_all: 655,
      call_resistance_0dte: 650,
      put_support_all: 620,
      put_support_0dte: 630,
      expected_high: 653,
      expected_low: 632,
      distance_to_hvl_pct: "1.16%",
      iv30d: 19.1,
      hv30: 16.7,
      iv_rank: "38%",
      top_gex_strikes: [635, 645, 650],
    },
    source_delta: {
      flip_vs_hvl: { uw: 640, mq: 635, delta: 5 },
      put_wall_vs_support_all: { uw: 620, mq: 620, delta: 0 },
      call_wall_vs_resistance_all: { uw: 660, mq: 655, delta: 5 },
    },
  };
}

export function buildDemoDispersionFixture(now: Date = new Date()): DispersionData {
  const date = lastCompletedSessionDate(now);
  const dates = sessionDatesEndingAt(126, date);
  const series: DispersionPoint[] = dates.map((session, index) => ({
    date: session,
    z_vix: round(0.15 + 0.85 * Math.sin(index / 13)),
    z_stock: round(0.75 + 1.05 * Math.sin(index / 15)),
    z_sector: round(0.55 + 0.9 * Math.cos(index / 17)),
    vix: round(17.8 + 3.2 * Math.sin(index / 12)),
    stock_spread: round(0.061 + 0.013 * Math.sin(index / 15), 4),
    sector_spread: round(0.021 + 0.006 * Math.cos(index / 17), 4),
  }));
  const last: DispersionPoint = {
    date,
    z_vix: -0.31,
    z_stock: 2.38,
    z_sector: 2.41,
    vix: 18.64,
    stock_spread: 0.0712,
    sector_spread: 0.0241,
  };
  series[series.length - 1] = last;

  return {
    scan_time: now.toISOString(),
    status: "ok",
    source: { prices: "stored", vix: "stored" },
    data_date: date,
    universe: {
      index: "SPX",
      n_constituents: 503,
      sectors: ["XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"],
    },
    fetch: { ib_ok: 0, yahoo_ok: 0, failed: 0, failed_symbols: [] },
    count: series.length,
    current: {
      ...last,
      m60_vix: 18.1,
      m60_stock: 0.064,
      m60_sector: 0.021,
      n_stocks: 501,
      n_sectors: 11,
      regime: "BELOW THE SURFACE",
      surface_gap: 2.72,
    },
    stats: {
      base: { start: dates[0], end: date, n: series.length },
      vix: { mean_60d: 18.1, stdev_60d: 3.9, z_min: -1.4, z_max: 2.8 },
      stock: { mean_60d: 0.064, stdev_60d: 0.013, z_min: -1.5, z_max: 3.2 },
      sector: { mean_60d: 0.021, stdev_60d: 0.006, z_min: -1.3, z_max: 2.9 },
      days_below_surface: 18,
      last_below_surface_date: date,
    },
    series,
  };
}

function trinState(value: number): TrinState {
  if (value <= 0.6) return "in_zone";
  if (value <= 0.65) return "near_zone";
  if (value >= 1.5) return "elevated";
  return "neutral";
}

export function buildDemoTrinFixture(now: Date = new Date()): TrinData {
  const date = mostRecentSessionDate(now);
  const scanTime = currentSessionTimestamp(now, date);
  const sessions = sessionDatesEndingAt(8, date);
  const hourly: TrinHourlyBar[] = sessions.flatMap((session, dayIndex) =>
    Array.from({ length: 7 }, (_, slot) => {
      const index = dayIndex * 7 + slot;
      const value = round(0.92 + 0.27 * Math.sin(index / 4));
      return {
        ts: `${session}T${String(14 + slot).padStart(2, "0")}:30:00.000Z`,
        bucket: `${session}T${String(9 + slot).padStart(2, "0")}:30`,
        trin: value,
        ma10: index < 9 ? null : round(0.94 + 0.08 * Math.sin(index / 8)),
      };
    }),
  );
  const latest = hourly[hourly.length - 1];
  const marketOpen = isMarketOpenAt(now) && etCalendarDate(now) === date;
  latest.ts = marketOpen ? now.toISOString() : etWallClockTimestamp(date, 15, 55);
  if (marketOpen) {
    const parts = new Intl.DateTimeFormat("en-US-u-hc-h23", {
      timeZone: ET_TIME_ZONE,
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(now);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    const minutes = Number(values.hour) * 60 + Number(values.minute);
    const bucketStart = 9 * 60 + 30 + Math.floor((minutes - (9 * 60 + 30)) / 60) * 60;
    latest.bucket = `${date}T${String(Math.floor(bucketStart / 60)).padStart(2, "0")}:${String(bucketStart % 60).padStart(2, "0")}`;
  }
  latest.trin = 0.82;
  latest.ma10 = 0.91;
  const daily = sessionDatesEndingAt(30, date).map((session, index) => ({
    date: session,
    close: session === date ? latest.trin : round(0.95 + 0.32 * Math.sin(index / 4)),
  }));

  return {
    scan_time: scanTime,
    source: "sample snapshot",
    current: {
      ts: latest.ts,
      session_date: date,
      trin: latest.trin,
      ma10: latest.ma10,
      state: trinState(latest.ma10 ?? latest.trin),
      adv: 1_742,
      dec: 1_318,
      up_vol: 2_180_000_000,
      down_vol: 1_352_000_000,
      daily_close: latest.trin,
      daily_date: date,
      zone_low: 0.6,
      zone_near: 0.65,
      zone_high: 1.5,
    },
    hourly,
    daily,
  };
}

const BPI_INDEX_NAMES: Record<BpiIndexSymbol, string> = {
  NDX: "Nasdaq-100",
  SPX: "S&P 500",
  RUT: "Russell 2000",
};

const BPI_MEMBERS: Record<BpiIndexSymbol, number> = { NDX: 100, SPX: 500, RUT: 1_950 };
const BPI_PHASE: Record<BpiIndexSymbol, number> = { NDX: 0, SPX: 11, RUT: 23 };

function buildDemoBpiIndex(symbol: BpiIndexSymbol, asOf: string, takenAt: string): BpiPayload {
  const dates = sessionDatesEndingAt(252, asOf);
  const phase = BPI_PHASE[symbol];
  const values = dates.map((_date, index) => round(47 + 21 * Math.sin((2 * Math.PI * (index + phase)) / 84)));
  const value = values[values.length - 1];
  const previous = values[values.length - 2];
  const members = BPI_MEMBERS[symbol];
  const state: BpiState = value <= 30 ? "OVERSOLD" : value >= 70 ? "OVERBOUGHT" : "NEUTRAL";
  return {
    schema_version: 1,
    index_symbol: symbol,
    index_name: BPI_INDEX_NAMES[symbol],
    taken_at: takenAt,
    as_of_session: asOf,
    bpi: value,
    members,
    bullish: Math.round((value / 100) * members),
    state,
    cross_up_30: previous < 30 && value >= 30,
    thresholds: { oversold: 30, overbought: 70 },
    history: dates.map((date, index) => ({ date, bpi: values[index] })),
    sources: { constituents: "sample snapshot", member_close_fetches: { sample_snapshot: members } },
  };
}

export function buildDemoBpiFixture(now: Date = new Date()): BpiResponse {
  const asOf = lastCompletedSessionDate(now);
  const takenAt = currentSessionTimestamp(now, asOf);
  const ndx = buildDemoBpiIndex("NDX", asOf, takenAt);
  return {
    generated_at: ndx.taken_at,
    indices: {
      NDX: ndx,
      SPX: buildDemoBpiIndex("SPX", asOf, takenAt),
      RUT: buildDemoBpiIndex("RUT", asOf, takenAt),
    },
  };
}
