/** Populated samples reused from the corresponding indicator E2E contracts. */

// Source: e2e/cor-tab.spec.ts
export function CorFixture() {
  function buildSeries() {
    const series: Array<{
      date: string;
      cor1m: number | null;
      cor3m: number | null;
      cor6m: number | null;
      cor1y: number | null;
    }> = [];
    const day = new Date(Date.UTC(2025, 5, 2));
    let i = 0;
    while (series.length < 300) {
      const dow = day.getUTCDay();
      if (dow !== 0 && dow !== 6) {
        series.push({
          date: day.toISOString().slice(0, 10),
          cor1m: 20 + (i % 9),
          cor3m: i % 11 === 0 ? null : 25 + (i % 7),
          cor6m: 30 + (i % 5),
          cor1y: 35 + (i % 3),
        });
        i += 1;
      }
      day.setUTCDate(day.getUTCDate() + 1);
    }
    return series;
  }

  const SERIES = buildSeries();

  const COR_MOCK = {
    scan_time: new Date().toISOString(),
    source_last_modified: {
      cor1m: "Fri, 07 Aug 2026 22:31:04 GMT",
      cor3m: "Fri, 07 Aug 2026 22:31:10 GMT",
      cor6m: "Fri, 07 Aug 2026 22:31:16 GMT",
      cor1y: "Fri, 07 Aug 2026 22:31:22 GMT",
    },
    count: SERIES.length,
    current: {
      date: SERIES[SERIES.length - 1].date,
      cor1m: 7.38,
      cor3m: 10.48,
      cor6m: 12.16,
      cor1y: 14.19,
      term_spread: 6.81,
      change_1d: { cor1m: 0.76, cor3m: 1.07, cor6m: 0.98, cor1y: 0.74 },
    },
    stats: {
      cor1m: { high: 96.59, low: 2.93, avg: 37.198579, stddev: 17.867378, percentile: 0.010037 },
      cor3m: { high: 90.79, low: 7.19, avg: 41.240333, stddev: 16.35408, percentile: 0.012582 },
      cor6m: { high: 86.35, low: 9.67, avg: 44.342579, stddev: 15.781472, percentile: 0.0083 },
      cor1y: { high: 79.77, low: 11.9, avg: 46.133582, stddev: 14.713988, percentile: 0.007721 },
    },
    series: SERIES,
  };
  return COR_MOCK;
}

// Source: e2e/skew-tab.spec.ts
export function SkewFixture() {
  function buildSeries() {
    const series: Array<{
      date: string;
      ratio: number;
      change: number | null;
    }> = [];
    const day = new Date(Date.UTC(2025, 5, 2));
    let i = 0;
    while (series.length < 300) {
      const dow = day.getUTCDay();
      if (dow !== 0 && dow !== 6) {
        series.push({
          date: day.toISOString().slice(0, 10),
          ratio: 1.3 + (i % 9) * 0.01,
          change: i === 0 ? null : ((i % 7) - 3) * 0.01,
        });
        i += 1;
      }
      day.setUTCDate(day.getUTCDate() + 1);
    }
    return series;
  }

  const SERIES = buildSeries();

  const SKEW_MOCK = {
    scan_time: new Date().toISOString(),
    source: "unusual_whales",
    market_status: "open",
    count: SERIES.length,
    current: {
      date: SERIES[SERIES.length - 1].date,
      ratio: 1.292999,
      change: -0.12,
      put_iv: 0.159567,
      call_iv: 0.123408,
      expiry: "2026-09-18",
      dte: 44,
      is_intraday: true,
      as_of: new Date().toISOString(),
    },
    stats: { high: 0.13, low: -0.16, avg: 0.0004, stddev: 0.04 },
    series: SERIES,
  };
  return SKEW_MOCK;
}

// Source: e2e/skew2d-tab.spec.ts
export function Skew2dFixture() {
  function buildSeries() {
    const series: Array<{
      date: string;
      ratio: number;
      change: number | null;
    }> = [];
    const day = new Date(Date.UTC(2025, 5, 2));
    let i = 0;
    while (series.length < 300) {
      const dow = day.getUTCDay();
      if (dow !== 0 && dow !== 6) {
        series.push({
          date: day.toISOString().slice(0, 10),
          ratio: 1.3 + (i % 9) * 0.01,
          change: i < 2 ? null : ((i % 7) - 3) * 0.01,
        });
        i += 1;
      }
      day.setUTCDate(day.getUTCDate() + 1);
    }
    return series;
  }

  const SERIES = buildSeries();

  const SKEW2D_MOCK = {
    scan_time: new Date().toISOString(),
    source: "skew_history",
    market_status: "closed",
    count: SERIES.length,
    current: {
      date: SERIES[SERIES.length - 1].date,
      ratio: 1.23838134631528,
      change: 0.08,
      put_iv: 0.15,
      call_iv: 0.12,
      expiry: "2026-08-21",
      dte: 14,
    },
    stats: { high: 0.27, low: -0.21, avg: 0, stddev: 0.03 },
    series: SERIES,
  };
  return SKEW2D_MOCK;
}

// Source: e2e/straddle-tab.spec.ts
export function StraddleFixture() {
  function buildSeries() {
    const series: Array<{
      date: string;
      spx_close: number;
      vix1d_close: number;
      ratio: number | null;
    }> = [];
    const day = new Date(Date.UTC(2025, 5, 2));
    let i = 0;
    while (series.length < 300) {
      const dow = day.getUTCDay();
      if (dow !== 0 && dow !== 6) {
        series.push({
          date: day.toISOString().slice(0, 10),
          spx_close: 6000 + i * 5,
          vix1d_close: 12 + (i % 5),
          ratio: i === 0 ? null : ((i % 7) - 3) * 0.9,
        });
        i += 1;
      }
      day.setUTCDate(day.getUTCDate() + 1);
    }
    return series;
  }

  const SERIES = buildSeries();

  const STRADDLE_MOCK = {
    scan_time: new Date().toISOString(),
    source_last_modified: {
      spx: "Wed, 05 Aug 2026 17:01:43 GMT",
      vix1d: "Wed, 05 Aug 2026 18:31:25 GMT",
    },
    count: SERIES.length,
    current: {
      date: SERIES[SERIES.length - 1].date,
      ratio: 3.775801,
      move_pct: 1.789619,
      implied_straddle_pct: 0.473971,
      spx_close: SERIES[SERIES.length - 1].spx_close,
      vix1d_prior: 9.43,
    },
    stats: { high: 3.775801, low: -4.968372, avg: 0.063255, stddev: 1.157428, hit_rate: 0.367675 },
    series: SERIES,
  };
  return STRADDLE_MOCK;
}

// Source: e2e/margin-debt-tab.spec.ts
export function MarginDebtFixture() {
  function buildSeries() {
    const months: string[] = [];
    for (let y = 2024; y <= 2026; y++) {
      for (let m = 1; m <= 12; m++) {
        const key = `${y}-${String(m).padStart(2, "0")}`;
        if (key >= "2024-06" && key <= "2026-05") months.push(key);
      }
    }
    return months.map((date, i) => {
      const level = 900_000 + i * 22_000;
      const lookback = i - 12;
      const yoy = lookback >= 0 ? (level / (900_000 + lookback * 22_000) - 1) * 100 : null;
      return {
        date,
        level,
        free_credit_cash: 200_000,
        free_credit_margin: 210_000,
        source: "finra",
        level_yoy_pct: date === "2026-05" ? 53.7 : yoy,
        level_display: level,
        net_level: level - 410_000,
        level_real: level,
        level_pct_gdp: 4.6,
        spx_close: 5000 + i * 100,
      };
    });
  }

  const SERIES = buildSeries();

  const MARGIN_DEBT_MOCK = {
    scan_time: new Date().toISOString(),
    source_last_modified: "Tue, 16 Jun 2026 14:52:10 GMT",
    count: SERIES.length,
    splice: { legacy_source: "nyse_legacy", ratio: 1.039, first_finra_month: "1997-01" },
    normalization: { available: true },
    current: { date: "2026-05", level: 1_415_557, level_yoy_pct: 53.7 },
    series: SERIES,
  };
  return MARGIN_DEBT_MOCK;
}

// Source: e2e/ivrank-tab.spec.ts
export function IvrankFixture() {
  function buildSeries() {
    const series: Array<{
      date: string;
      iv: number;
      iv_rank: number | null;
      iv_pct: number | null;
    }> = [];
    const day = new Date(Date.UTC(2025, 4, 12));
    let i = 0;
    while (series.length < 320) {
      const dow = day.getUTCDay();
      if (dow !== 0 && dow !== 6) {
        const preWindow = i < 60;
        series.push({
          date: day.toISOString().slice(0, 10),
          iv: 0.11 + (i % 17) * 0.004,
          iv_rank: preWindow ? null : 8 + (i % 60),
          iv_pct: preWindow ? null : 10 + (i % 55),
        });
        i += 1;
      }
      day.setUTCDate(day.getUTCDate() + 1);
    }
    return series;
  }

  const SERIES = buildSeries();

  const LAST = SERIES[SERIES.length - 1];

  const IVRANK_MOCK = {
    scan_time: new Date().toISOString(),
    status: "ok",
    source: "ib",
    as_of: LAST.date,
    expected_session: LAST.date,
    market_status: "closed",
    rank_window: 252,
    count: SERIES.length,
    rank_count: SERIES.filter((entry) => entry.iv_rank != null).length,
    current: {
      date: LAST.date,
      iv: 0.12201147,
      iv_rank: 10.559822,
      iv_pct: 11.952191,
      iv_1y_low: 0.10542261,
      iv_1y_high: 0.26251674,
      rank_change_1d: -1.2,
      regime: "SUPPRESSED",
    },
    uw_check: { date: LAST.date, iv_rank: 10.58 },
    stats: {
      min: 0,
      p25: 18.4,
      median: 41.2,
      p75: 66,
      max: 100,
      mean: 43.1,
      share_suppressed: 0.24,
      share_extreme: 0.06,
    },
    series: SERIES,
  };
  return IVRANK_MOCK;
}

// Source: e2e/iv-spread-tab.spec.ts
export function IvSpreadFixture() {
  function buildSeries() {
    const series: Array<{
      date: string;
      spx_iv: number;
      ndx_iv: number;
      spread: number | null;
    }> = [];
    const day = new Date(Date.UTC(2025, 5, 10));
    let i = 0;
    while (series.length < 320) {
      const dow = day.getUTCDay();
      if (dow !== 0 && dow !== 6) {
        const spx = 0.12 + (i % 13) * 0.002;
        const ndx = 0.17 + (i % 11) * 0.003;
        series.push({
          date: day.toISOString().slice(0, 10),
          spx_iv: spx,
          ndx_iv: ndx,
          spread: i === 160 ? null : (ndx - spx) * 100,
        });
        i += 1;
      }
      day.setUTCDate(day.getUTCDate() + 1);
    }
    return series;
  }

  const SERIES = buildSeries();

  const LAST = SERIES[SERIES.length - 1];

  const IV_SPREAD_MOCK = {
    scan_time: new Date().toISOString(),
    status: "ok",
    source: "ib",
    as_of: LAST.date,
    expected_session: LAST.date,
    market_status: "closed",
    count: SERIES.length,
    spread_count: SERIES.filter((entry) => entry.spread != null).length,
    dropped_unpaired: 0,
    current: {
      date: LAST.date,
      spx_iv: 0.12104312,
      ndx_iv: 0.1758578,
      spread: 5.481468,
      z_score: 0.104002,
      pctile: 59.377494,
      change_1d: 0.360352,
      regime: "NORMAL",
    },
    stats: {
      count: 1253,
      high: 12.642458,
      high_date: "2026-06-23",
      low: -3.297135,
      low_date: "2025-04-08",
      mean: 5.318448,
      stdev: 1.567474,
      last: 5.481468,
    },
    excluded: [],
    series: SERIES,
  };
  return IV_SPREAD_MOCK;
}

// Source: e2e/vixts-tab.spec.ts
export function VixtsFixture() {
  const SERIES_LENGTH = 600;

  function daysAgo(n: number): string {
    const d = new Date();
    d.setUTCDate(d.getUTCDate() - n);
    return d.toISOString().slice(0, 10);
  }

  const DATA_DATE = daysAgo(1);

  function buildSeries() {
    const points = [];
    for (let i = 0; i < SERIES_LENGTH; i++) {
      points.push({
        date: i === SERIES_LENGTH - 1 ? DATA_DATE : daysAgo(SERIES_LENGTH - i),
        vix: 15 + 3 * Math.sin(i / 20),
        vix3m: 18 + 2 * Math.sin(i / 30),
        // Smooth, always inside the plausible 0.40..2.50 band, never NaN.
        ratio: 0.88 + 0.12 * Math.sin(i / 25),
        spx: 6800 + 900 * Math.sin(i / 40),
      });
    }
    points[SERIES_LENGTH - 1] = {
      date: DATA_DATE,
      vix: 15.21,
      vix3m: 17.99,
      ratio: 0.8455,
      spx: 7654.32,
    };
    return points;
  }

  const VIXTS_MOCK = {
    scan_time: new Date().toISOString(),
    source_last_modified: {
      vix: "Thu, 27 Aug 2026 01:50:46 GMT",
      vix3m: "Wed, 26 Aug 2026 22:00:57 GMT",
      spx: "Thu, 27 Aug 2026 00:31:07 GMT",
    },
    data_date: DATA_DATE,
    count: SERIES_LENGTH,
    current: {
      date: DATA_DATE,
      vix: 15.21,
      vix3m: 17.99,
      ratio: 0.8455,
      regime: "CONTANGO",
      spx: 7654.32,
    },
    stats: {
      min: 0.7104,
      max: 1.3437,
      mean: 0.894398,
      median: 0.8846,
      days_backwardation: 325,
      pct_backwardation: 7.6435,
      last_backwardation_date: daysAgo(140),
    },
    series: buildSeries(),
  };
  return VIXTS_MOCK;
}

// Source: e2e/vixcor-tab.spec.ts
export function VixcorFixture() {
  function buildSeries() {
    const series: Array<{
      date: string;
      vix_close: number;
      cor3m_close: number;
      corr20: number | null;
      episode: boolean;
    }> = [];
    const day = new Date(Date.UTC(2025, 4, 12));
    let i = 0;
    while (series.length < 320) {
      const dow = day.getUTCDay();
      if (dow !== 0 && dow !== 6) {
        const decoupled = i >= 300;
        const midEpisode = i >= 150 && i <= 158;
        series.push({
          date: day.toISOString().slice(0, 10),
          vix_close: 14 + (i % 13) * 0.4,
          cor3m_close: 11 + (i % 9) * 0.5,
          corr20:
            i < 19
              ? null
              : decoupled
                ? 0.24 - (i - 300) * 0.02
                : midEpisode
                  ? 0.18
                  : 0.72 + (i % 7) * 0.03,
          episode: decoupled || midEpisode,
        });
        i += 1;
      }
      day.setUTCDate(day.getUTCDate() + 1);
    }
    return series;
  }

  const SERIES = buildSeries();

  const LAST = SERIES[SERIES.length - 1];

  const EPISODES = [
    {
      trigger: SERIES[150].date,
      start: SERIES[150].date,
      end: SERIES[158].date,
      sessions: 9,
      trough: 0.18,
      trough_date: SERIES[152].date,
      corr_at_trigger: 0.18,
      vix_at_trigger: 16.29,
      open: false,
      forward: { "5": 0.058, "10": 0.33, "21": 0.554, "42": 0.622, "63": null },
    },
    {
      trigger: SERIES[300].date,
      start: SERIES[300].date,
      end: LAST.date,
      sessions: 20,
      trough: LAST.corr20 ?? 0.01,
      trough_date: LAST.date,
      corr_at_trigger: 0.24,
      vix_at_trigger: 15.28,
      open: true,
      forward: { "5": null, "10": null, "21": null, "42": null, "63": null },
    },
  ];

  function bucket(mean: number, n: number) {
    return { n, mean_drawup: mean, median_drawup: mean * 0.8, p_higher: 0.46, p_drawup_20: 0.15 };
  }

  const VIXCOR_MOCK = {
    scan_time: new Date().toISOString(),
    source_last_modified: { vix: "Sat, 15 Aug 2026 23:01:11 GMT" },
    status: "ok",
    as_of: LAST.date,
    parent_as_of: LAST.date,
    vix_as_of: LAST.date,
    expected_session: LAST.date,
    lag_sessions: 0,
    market_status: "closed",
    window: 20,
    count: SERIES.length,
    corr_count: SERIES.filter((entry) => entry.corr20 != null).length,
    current: {
      date: LAST.date,
      vix_close: LAST.vix_close,
      cor3m_close: LAST.cor3m_close,
      corr20: LAST.corr20,
      change_1d: -0.0148,
      percentile: 0.0186,
      regime: "DECOUPLED",
      vix_cov_20d: 0.1064,
      episode: EPISODES[1],
    },
    stats: {
      min: -0.5324,
      p01: -0.1135,
      p05: 0.2586,
      p10: 0.457,
      p25: 0.6795,
      median: 0.8446,
      p75: 0.925,
      p90: 0.9582,
      p95: 0.9723,
      p99: 0.9857,
      max: 0.9932,
      mean: 0.7621,
      stddev: 0.2341,
      share_below_zero: 0.0177,
      share_below_trigger: 0.0489,
      vix_cov_breakdown: 0.0691,
      vix_cov_coupled: 0.118,
    },
    episodes: EPISODES,
    forward_stats: {
      horizons: [5, 10, 21, 42, 63],
      event: {
        "5": bucket(0.0392, 30),
        "10": bucket(0.08, 30),
        "21": bucket(0.2089, 30),
        "42": bucket(0.3301, 30),
        "63": bucket(0.4392, 29),
      },
      base: {
        "5": bucket(0.0896, 5147),
        "10": bucket(0.1582, 5142),
        "21": bucket(0.2736, 5131),
        "42": bucket(0.4401, 5110),
        "63": bucket(0.5726, 5089),
      },
    },
    series: SERIES,
  };
  return VIXCOR_MOCK;
}

// Source: e2e/hyad-tab.spec.ts
export function HyadFixture() {
  const DAY_MS = 86_400_000;

  const SERIES_LENGTH = 240;

  function isoDaysAgo(days: number): string {
    return new Date(Date.now() - days * DAY_MS).toISOString().slice(0, 10);
  }

  const DATA_DATE = isoDaysAgo(1);

  function buildSeries() {
    const points = [];
    let cum = 0;
    for (let i = 0; i < SERIES_LENGTH; i++) {
      const net = Math.round(500 * Math.sin(i / 12));
      cum += net;
      points.push({
        date: i === SERIES_LENGTH - 1 ? DATA_DATE : isoDaysAgo(SERIES_LENGTH - i),
        net,
        cum,
        ma21: i >= 20 ? cum - 40 : null,
        ma50: i >= 49 ? cum - 90 : null,
        spx_close: i % 11 === 0 ? null : 5200 + 3 * i,
      });
    }
    return points;
  }

  const SERIES = buildSeries();

  const HYAD_MOCK = {
    scan_time: new Date().toISOString(),
    data_date: DATA_DATE,
    current: {
      date: DATA_DATE,
      advances: 1227,
      declines: 1504,
      unchanged: 69,
      total: 3163,
      net: -277,
      cum: -2535,
      ma21: -1010.4,
      ma50: 850.2,
    },
    series: SERIES,
  };
  return HYAD_MOCK;
}

// Source: e2e/hhlev-tab.spec.ts
export function HhlevFixture() {
  const SERIES_LENGTH = 320;

  function quarterStart(quartersBack: number): string {
    const now = new Date();
    const total = now.getUTCFullYear() * 4 + Math.floor(now.getUTCMonth() / 3) - quartersBack;
    const year = Math.floor(total / 4);
    const month = (total % 4) * 3 + 1;
    return `${year}-${String(month).padStart(2, "0")}-01`;
  }

  const DATA_DATE = quarterStart(1);

  const DATA_QUARTER_LABEL = (() => {
    const [year, month] = DATA_DATE.split("-").map(Number);
    return `${year} Q${Math.floor((month - 1) / 3) + 1}`;
  })();

  function buildSeries() {
    const points = [];
    for (let i = 0; i < SERIES_LENGTH; i++) {
      points.push({
        date: i === SERIES_LENGTH - 1 ? DATA_DATE : quarterStart(SERIES_LENGTH - i),
        // Smooth, inside the plausible 2..40 band, never NaN.
        leverage_pct: 12 + 8 * Math.sin(i / 16),
      });
    }
    points[SERIES_LENGTH - 1].leverage_pct = 11.78;
    return points;
  }

  const HHLEV_MOCK = {
    scan_time: new Date().toISOString(),
    source_last_modified: quarterStart(1),
    data_date: DATA_DATE,
    current: {
      date: DATA_DATE,
      leverage_pct: 11.78,
      liabilities_musd: 21560050,
      net_worth_musd: 182979889,
    },
    series: buildSeries(),
  };
  return HHLEV_MOCK;
}

// Source: e2e/ma-ratio-tab.spec.ts
export function MaRatioFixture() {
  const DAY_MS = 86_400_000;

  const SERIES_LENGTH = 240;

  function isoDaysAgo(days: number): string {
    return new Date(Date.now() - days * DAY_MS).toISOString().slice(0, 10);
  }

  const DATA_DATE = isoDaysAgo(1);

  function buildSeries() {
    const points = [];
    for (let i = 0; i < SERIES_LENGTH; i++) {
      const ratio = Number((0.55 + 0.5 * Math.abs(Math.sin(i / 30))).toFixed(4));
      points.push({
        date: i === SERIES_LENGTH - 1 ? DATA_DATE : isoDaysAgo(SERIES_LENGTH - i),
        pct_above_50: Number((35 + 30 * Math.abs(Math.sin(i / 30))).toFixed(2)),
        pct_above_200: Number((55 + 15 * Math.abs(Math.cos(i / 45))).toFixed(2)),
        ratio,
        spx_close: Number((5000 + i * 11.5).toFixed(2)),
      });
    }
    return points;
  }

  const SERIES = buildSeries();

  const MA_RATIO_MOCK = {
    schema_version: 1,
    scan_time: new Date().toISOString(),
    data_date: DATA_DATE,
    source: { constituents: "cache", constituents_count: 503, member_close_fetches: { yahoo: 490, stored: 13 } },
    zone: { low: 0.25, high: 0.5 },
    current: {
      ...SERIES[SERIES.length - 1],
      pct_above_50: 46.5,
      pct_above_200: 64.6,
      ratio: 0.72,
      count_above_50: 234,
      count_above_200: 325,
      eligible_50: 503,
      eligible_200: 503,
      spx_close: 7631.47,
    },
    series: SERIES,
    missing: false,
  };
  return MA_RATIO_MOCK;
}

// Source: e2e/credit-spread-tab.spec.ts
export function CreditSpreadFixture() {
  function buildSeries() {
    const rows = [];
    for (let i = 0; i < 24; i++) {
      const day = new Date(Date.UTC(2026, 6, 28 + i));
      rows.push({
        date: day.toISOString().slice(0, 10),
        hyg_close: Number((80.6 - i * 0.04).toFixed(2)),
        spx_close: Number((6816 + i * 35).toFixed(2)),
      });
    }
    const last = rows[rows.length - 1];
    last.date = "2026-08-20";
    last.hyg_close = 79.56;
    last.spx_close = 7641.16;
    return rows;
  }

  const SERIES = buildSeries();

  const CREDIT_MOCK = {
    scan_time: new Date().toISOString(),
    source: "ib",
    count: SERIES.length,
    current: {
      date: "2026-08-20",
      hyg_close: 79.55999755859375,
      spx_close: 7641.16015625,
      hyg_ret: -0.013025716955806343,
      spx_ret: 0.12097839201868865,
      regime: "divergent",
      near_high: true,
    },
    series: SERIES,
  };
  return CREDIT_MOCK;
}

// Source: e2e/iei-hyg-tab.spec.ts
export function IeiHygFixture() {
  function buildSeries() {
    const rows = [];
    for (let i = 0; i < 62; i++) {
      const day = new Date(Date.UTC(2026, 4, 26 + i));
      const iei = Number((117 - i * 0.01).toFixed(4));
      const hyg = Number((80 + i * 0.005).toFixed(4));
      rows.push({
        date: day.toISOString().slice(0, 10),
        iei_close: iei,
        hyg_close: hyg,
        dxy_close: i % 7 === 3 ? null : Number((99 - i * 0.005).toFixed(4)),
        ratio: iei / hyg,
      });
    }
    const last = rows[rows.length - 1];
    last.date = "2026-08-21";
    last.iei_close = 116.41;
    last.hyg_close = 79.61;
    last.dxy_close = 98.8;
    last.ratio = 1.462253520532856;
    return rows;
  }

  const SERIES = buildSeries();

  const IEI_HYG_MOCK = {
    scan_time: new Date().toISOString(),
    source: "ib",
    count: SERIES.length,
    current: {
      date: "2026-08-21",
      iei_close: 116.41,
      hyg_close: 79.61,
      dxy_close: 98.8,
      ratio: 1.462253520532856,
      ratio_52w_low: 1.462253520532856,
      low_date: "2026-08-21",
      ratio_52w_high: 1.475760927676247,
      high_date: "2026-06-26",
      ratio_pct_rank: 0,
      window_sessions: 62,
      state: "new_low",
    },
    series: SERIES,
  };
  return IEI_HYG_MOCK;
}

// Source: e2e/divyield-tab.spec.ts
export function DivyieldFixture() {
  const DAY_MS = 86_400_000;

  const SERIES_LENGTH = 240;

  function isoDaysAgo(days: number): string {
    return new Date(Date.now() - days * DAY_MS).toISOString().slice(0, 10);
  }

  const DATA_DATE = isoDaysAgo(1);

  function buildSeries() {
    const points = [];
    for (let i = 0; i < SERIES_LENGTH; i++) {
      const total = 480 + (i % 24);
      const pct = Number((5 + 20 * Math.abs(Math.sin(i / 40))).toFixed(2));
      points.push({
        date: i === SERIES_LENGTH - 1 ? DATA_DATE : isoDaysAgo(SERIES_LENGTH - i),
        pct_above: pct,
        count_above: Math.round((pct / 100) * total),
        total,
        y10: Number((4 + (i % 10) * 0.1).toFixed(2)),
        approximate: i < SERIES_LENGTH / 2 ? 1 : 0,
      });
    }
    return points;
  }

  const SERIES = buildSeries();

  const DIVYIELD_MOCK = {
    scan_time: new Date().toISOString(),
    source: { constituents: "github-datasets", constituents_count: 503, quote_errors: 0 },
    data_date: DATA_DATE,
    y10_date: DATA_DATE,
    current: {
      date: DATA_DATE,
      pct_above: 3.78,
      count_above: 19,
      total: 503,
      y10: 4.74,
      leaders: [{ ticker: "TDG", yield_pct: 7.5 }],
    },
    series: SERIES,
    backfill_cutover: SERIES[Math.floor(SERIES_LENGTH / 2)].date,
  };
  return DIVYIELD_MOCK;
}

// Source: e2e/streaks-tab.spec.ts
export function StreaksFixture(symbol = "SPY") {
  function buildSeries() {
    const series: Array<{ date: string; close: number; streak: number }> = [];
    const start = Date.UTC(2026, 4, 1);
    let close = 100;
    let streak = 0;
    for (let i = 0; i < 60; i += 1) {
      if (i > 0) {
        const up = i % 4 === 1 || i % 4 === 2 || i === 59;
        close = up ? close + 1 : close - 0.5;
        streak = up ? streak + 1 : 0;
      }
      const date = new Date(start + i * 86_400_000).toISOString().slice(0, 10);
      series.push({ date, close: Number(close.toFixed(2)), streak });
    }
    return series;
  }

  function buildPayload(symbol: string) {
    const series = buildSeries();
    const last = series[series.length - 1];
    return {
      symbol,
      scan_time: new Date().toISOString(),
      source: "yahoo",
      missing: false,
      count: series.length,
      first_date: series[0].date,
      last_date: last.date,
      current: {
        date: last.date,
        close: last.close,
        streak: last.streak,
        day_change_pct: 0.42,
      },
      stats: {
        max_streak: 3,
        max_streak_end: last.date,
        runs_total: 15,
        runs_ge_current: 1,
        avg_run: 1.93,
        up_day_pct: 52.5,
      },
      series,
    };
  }
  return buildPayload(symbol);
}

// Source: e2e/curve-tab.spec.ts
export function CurveFixture() {
  function buildSeries() {
    const rows = [];
    for (let i = 0; i < 24; i++) {
      const day = new Date(Date.UTC(2026, 6, 8 + i));
      const y2 = 4.1 + i * 0.008;
      const y10 = 4.7 + i * 0.003;
      rows.push({
        date: day.toISOString().slice(0, 10),
        y3m: 3.83,
        y2: Number(y2.toFixed(2)),
        y10: Number(y10.toFixed(2)),
        spread: Number((y10 - y2).toFixed(4)),
        spx_close: 7400 + i * 8,
      });
    }
    const last = rows[rows.length - 1];
    last.y2 = 4.28;
    last.y10 = 4.75;
    last.spread = 0.47;
    return rows;
  }

  const SERIES = buildSeries();

  const YIELD_CURVE_MOCK = {
    scan_time: new Date().toISOString(),
    source: "treasury",
    count: SERIES.length,
    current: { date: "2026-07-31", y3m: 3.83, y2: 4.28, y10: 4.75, spread: 0.47 },
    series: SERIES,
  };
  return YIELD_CURVE_MOCK;
}
