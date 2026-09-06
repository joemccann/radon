/** Source-shaped samples reused from existing workflow contracts. */
// Source: e2e/vol-cone-tab.spec.ts
export function VolConeFixture() {
  function point(date: string, atm: number) {
    return {
      date,
      spot: 220,
      atm_iv: atm,
      call_10_iv: atm - 0.005,
      put_10_iv: atm + 0.01,
    };
  }

  function name(overrides: Record<string, unknown> = {}) {
    const series = Array.from({ length: 18 }, (_, i) =>
      point(`2026-04-${String(10 + i).padStart(2, "0")}`, 0.40 - i * 0.001),
    );
    return {
      ticker: "NVDA",
      spot: 223.95,
      expiry: "2026-09-18",
      dte: 37,
      atm_iv: 0.3851329156797111,
      call_10_iv: 0.3862120615005326,
      put_10_iv: 0.39731998999142565,
      call_10_strike: 246.345,
      put_10_strike: 201.555,
      p10: 0.3879,
      p90: 0.443,
      atm_percentile: 0,
      call_10_percentile: 0.0556,
      put_10_percentile: 0.1111,
      wing_score: 0.0833,
      regime: "CHEAP_WINGS",
      series,
      ...overrides,
    };
  }

  const NVDA = name();

  const SMH = name({
    ticker: "SMH",
    regime: "NEUTRAL",
    wing_score: 0.44,
    atm_percentile: 0.4,
    atm_iv: 0.387,
  });

  const VOL_CONE_MOCK = {
    scan_time: "2026-08-12T20:45:00Z",
    source_as_of: "2026-08-12",
    count: 2,
    hit_count: 1,
    current: NVDA,
    names: [NVDA, SMH],
    hits: [NVDA],
  };
  return VOL_CONE_MOCK;
}

// Source: e2e/scanner-discover.spec.ts
export function DiscoverFixture() {
  const discoverPayload = {
    discovery_time: "2026-06-24T15:05:00Z",
    alerts_analyzed: 7,
    candidates_found: 1,
    candidates: [
      {
        ticker: "MSFT",
        score: 72.5,
        score_breakdown: {},
        alerts: 3,
        total_premium: 1_250_000,
        calls: 8,
        puts: 1,
        options_bias: "BULLISH",
        sweeps: 2,
        avg_vol_oi: 4.2,
        sector: "Technology",
        issue_type: "Common Stock",
        dp_direction: "ACCUMULATION",
        dp_strength: 64.1,
        dp_buy_ratio: 0.68,
        dp_sustained_days: 2,
        dp_total_prints: 19,
        confluence: true,
      },
    ],
  };
  return discoverPayload;
}

// Source: e2e/scanner-ticker-scan.spec.ts
export function LeapFixture() {
  const LEAP_SCANNED = {
    scan_time: "2026-07-05T15:00:00Z",
    min_gap: 10,
    universe: "explicit",
    requested_tickers: ["NVDA", "AMD"],
    results: [
      {
        ticker: "NVDA",
        price: 181.4,
        hv_20: 42.1,
        hv_60: 38.7,
        hv_252: 44.9,
        current_iv: 31.2,
        iv_rank: 12.5,
        leap_count: 8,
        best_gap: 13.7,
        is_mispriced: true,
      },
      {
        ticker: "AMD",
        price: 140.2,
        hv_20: 38.3,
        hv_60: 36.1,
        hv_252: 41.4,
        current_iv: 35.9,
        iv_rank: 24.0,
        leap_count: 5,
        best_gap: 2.4,
        is_mispriced: false,
      },
    ],
  };
  return LEAP_SCANNED;
}

// Source: e2e/scanner-ticker-scan.spec.ts
export function GarchFixture() {
  const GARCH_SCANNED = {
    scan_time: "2026-07-05T15:05:00Z",
    universe: "explicit",
    requested_tickers: ["NVDA", "AMD"],
    tickers: {},
    pairs: [
      {
        pair: ["NVDA", "AMD"],
        leader: "NVDA",
        lagger: "AMD",
        divergence: 2.41,
        lagger_hv_iv_gap: 9.8,
        lagger_iv_rank: 15.0,
        signal: "STRONG",
        gates_passed: true,
        failing_gates: [],
        expected_iv: 47.2,
        expected_move: 6.1,
      },
    ],
  };
  return GARCH_SCANNED;
}

// Source: e2e/cta-page.spec.ts
export function CtaFixture() {
  const CTA_MOCK = {
    date: "2026-03-09",
    fetched_at: "2026-03-09T16:45:00Z",
    source: "menthorq_s3_vision",
    tables: {
      main: [
        { underlying: "SPX", position_today: 0.45, position_yesterday: 0.42, position_1m_ago: 0.60, percentile_1m: 13, percentile_3m: 18, percentile_1y: 22, z_score_3m: -1.56 },
        { underlying: "NQ", position_today: 0.38, position_yesterday: 0.40, position_1m_ago: 0.55, percentile_1m: 20, percentile_3m: 25, percentile_1y: 30, z_score_3m: -1.20 },
      ],
      index: [
        { underlying: "ES", position_today: 0.50, position_yesterday: 0.48, position_1m_ago: 0.65, percentile_1m: 15, percentile_3m: 20, percentile_1y: 28, z_score_3m: -1.40 },
      ],
      commodity: [],
      currency: [],
    },
    cache_meta: {
      last_refresh: "2026-03-09T16:45:00Z",
      age_seconds: 120,
      is_stale: false,
      stale_threshold_seconds: null,
      target_date: "2026-03-09",
      latest_cache_date: "2026-03-09",
      stale_reason: "fresh",
    },
    sync_status: {
      service: "cta-sync",
      status: "success",
      trigger: "launchd",
      target_date: "2026-03-09",
      started_at: "2026-03-09T16:40:00Z",
      finished_at: "2026-03-09T16:45:00Z",
      duration_ms: 30_000,
      attempt_count: 1,
      cache_path: "data/menthorq_cache/cta_2026-03-09.json",
      error_type: null,
      error_excerpt: null,
      artifact_log_path: null,
    },
  };
  return CTA_MOCK;
}

// Source: e2e/mobile-executed-journal.spec.ts
export function JournalFixture() {
  const TODAY = "2026-05-06";

  const JOURNAL = {
    trades: [
      {
        id: 42,
        date: TODAY,
        ticker: "MSFT",
        structure: "Long Call ($410)",
        decision: "OPEN",
        contracts: 3,
        entry_cost: 900,
        max_risk: 900,
        realized_pnl: null,
        return_on_risk: null,
        legs: [],
      },
      {
        id: 41,
        date: "2026-04-20",
        close_date: "2026-04-25",
        ticker: "NVDA",
        structure: "Bull Call Spread",
        decision: "CLOSED",
        contracts: 5,
        entry_cost: 1200,
        max_risk: 1200,
        realized_pnl: 380,
        return_on_risk: 0.317,
        legs: [],
      },
    ],
  };
  return JOURNAL;
}

// Source: tests/preferences-api.test.ts
export function PreferencesFixture() {
  const ENTRY = {
    key: "RADON_MAX_ORDER_QTY",
    label: "Max contracts per order",
    group: "Order Limits",
    value_type: "int",
    value: 400,
    default: 500,
    hard_min: 1,
    hard_max: 5000,
    unit: "contracts",
    description: "Hard ceiling on contracts per options or combo order.",
    applies_immediately: true,
    source: "db",
    db_rejected: false,
    updated_at: "2026-08-11T17:02:11Z",
    updated_by: "user_x",
  };

  const STORE = { available: true, error: null, checked_at: "2026-08-11T17:02:11Z" };

  const PAYLOAD = {
    preferences: [ENTRY],
    groups: ["Order Limits", "Scanning", "Feature Flags", "IB Recovery", "Health Monitoring"],
    store: STORE,
    generated_at: "2026-08-11T17:02:11Z",
  };
  return PAYLOAD;
}
