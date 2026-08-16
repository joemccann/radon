/**
 * v2 performance payload contract: normalizer key retention, degradation
 * semantics, benchmark coherence, and the route's status/HTTP behaviour.
 *
 * RED-first. Spec references: §C.1 (status enum), §C.2 (the three gates),
 * §C.4 (structured warnings), §C.5 (payload v2), §C.6 (route always 200),
 * §E.9 tests 78, 84, 86, 87. Acceptance: F3, F4, F5.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildPerformanceView, normalizePerformanceData } from "../lib/performanceData";
import { buildPerformanceChartModel } from "../lib/performanceChart";
import { TWR_GATES } from "../lib/performanceTwr";
import {
  benchmarkUnavailablePayload,
  flowDoubledNavZeroTwrPayload,
  flowsFailedDegradedPayload,
  goldenOkPayload,
  insufficientDataPayload,
  legacyV1DeclaredOkPayload,
  legacyV1LivePayload,
  livePathologicalPayload,
  staleDiskCachePayload,
  suspectSubperiodDegradedPayload,
  unavailablePayload,
  LIVE_DEFECT_VALUES,
  type PerformancePayloadV2,
} from "./fixtures/performanceScenarios";

const BENCHMARK_DERIVED_KEYS = [
  "beta",
  "alpha",
  "alpha_annualized",
  "tracking_error",
  "information_ratio",
  "correlation",
  "r_squared",
  "benchmark_return",
  "benchmark_close",
  "benchmark_total_return",
];

function normalized(payload: PerformancePayloadV2) {
  const result = normalizePerformanceData(payload);
  if (!result) throw new Error("normalizePerformanceData returned null for a v2 payload");
  return result as unknown as Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// §E.9 test 78 — key retention through normalizePerformanceData
// ---------------------------------------------------------------------------

describe("normalizePerformanceData v2 key retention", () => {
  it("round-trips every v2 top-level key the UI branches on", () => {
    const payload = goldenOkPayload();
    const out = normalized(payload);

    expect(out.status).toBe("ok");
    expect(out.nav_source).toBe("flex_live");
    expect(out.nav_sessions_behind).toBe(0);
    expect(out.nav_as_of).toBe("2026-03-27");
    expect(out.flows_status).toBe("ok");
    expect(out.schema_version).toBe(2);
    expect(out.counts).toEqual({
      n_nav_observations: 61,
      n_subperiods: 60,
      n_returns: 60,
      n_skipped: 0,
      n_suspect: 0,
    });
    expect(Array.isArray(out.subperiods)).toBe(true);
    expect((out.subperiods as unknown[]).length).toBe(1);
    expect(out.generated_at).toBe("2026-03-27T21:05:00.000000Z");
  });

  it("keeps structured warning objects instead of flattening them to strings", () => {
    const out = normalized(flowsFailedDegradedPayload());
    const warnings = out.warnings as Array<Record<string, unknown>>;

    expect(warnings).toHaveLength(1);
    expect(warnings[0].code).toBe("FLOWS_FETCH_FAILED");
    expect(warnings[0].severity).toBe("error");
    expect(typeof warnings[0].message).toBe("string");
    expect(warnings[0].context).toEqual({ reason: "timeout" });
  });

  it("keeps twr / mwr / risk GatedValues intact", () => {
    const out = normalized(goldenOkPayload());
    const twr = out.twr as Record<string, unknown>;
    const mwr = out.mwr as Record<string, Record<string, unknown>>;
    const risk = out.risk as Record<string, Record<string, unknown>>;

    expect(twr.cum_return).toBe(0.05092267338835921);
    expect((twr.annualized as Record<string, unknown>).value).toBeNull();
    expect((twr.annualized as Record<string, unknown>).unavailable_reason).toBe("period_lt_1y");
    expect(mwr.period_return.value).toBe(0.05020769210515868);
    expect(mwr.annualized.unavailable_reason).toBe("period_lt_1y");
    expect(risk.sharpe_ratio.value).toBe(2.0104270378243907);
    expect(risk.sharpe_ratio.low_confidence).toBe(true);
  });

  it("reports investment P&L net of external flows, not the raw NAV delta", () => {
    const out = normalized(goldenOkPayload());
    const equity = out.equity as Record<string, number>;

    // 182217.35574011656 - 100000.00 - 75000.00 = 7217.35574011656
    expect(equity.investment_pnl).toBeCloseTo(7217.35574011656, 8);
    expect(equity.net_external_flows).toBe(75000.0);
    // Anti-pin: the raw delta is 82217.35574011656 and is NOT P&L.
    expect(equity.investment_pnl).not.toBeCloseTo(82217.35574011656, 2);
  });

  it("keeps the TWR index alongside dollar NAV on every series point", () => {
    const out = normalized(goldenOkPayload());
    const series = out.series as Array<Record<string, unknown>>;

    expect(series).toHaveLength(3);
    expect(series[0].twr_index).toBe(100);
    // 100 * (1 + 0.05092267338835921)
    expect(series[2].twr_index).toBeCloseTo(105.09226733883592, 10);
    expect(series[2].nav).toBe(182217.35574011656);
  });
});

// ---------------------------------------------------------------------------
// §C.2 Gate 3 / F5 — benchmark coherence
// ---------------------------------------------------------------------------

describe("benchmark coherence", () => {
  it("exposes the benchmark as a complete block, not a bare symbol string", () => {
    const out = normalized(goldenOkPayload());
    const benchmark = out.benchmark as Record<string, unknown>;

    expect(benchmark).not.toBeNull();
    expect(benchmark.symbol).toBe("SPY");
    expect(benchmark.beta).toBe(0.84);
    expect(benchmark.benchmark_return).toBe(0.0312);
    expect(benchmark.n_common).toBe(60);
  });

  it("emits ZERO benchmark-derived keys anywhere when benchmark is null", () => {
    const out = normalized(benchmarkUnavailablePayload());
    expect(out.benchmark).toBeNull();

    const json = JSON.stringify(out);
    for (const key of BENCHMARK_DERIVED_KEYS) {
      expect(json, `benchmark-derived key "${key}" leaked`).not.toContain(`"${key}"`);
    }
  });

  it("never publishes beta while the benchmark return is unavailable", () => {
    // The live payload rendered BETA 23.93 next to BENCHMARK RETURN "--".
    for (const payload of [goldenOkPayload(), benchmarkUnavailablePayload(), flowsFailedDegradedPayload()]) {
      const out = normalized(payload);
      const benchmark = out.benchmark as Record<string, unknown> | null;
      const hasBeta = benchmark != null && typeof benchmark.beta === "number";
      const hasReturn = benchmark != null && typeof benchmark.benchmark_return === "number";
      expect(hasBeta).toBe(hasReturn);
    }
  });
});

// ---------------------------------------------------------------------------
// §E.9 test 84 — the chart never fabricates a benchmark line
// ---------------------------------------------------------------------------

describe("buildPerformanceChartModel benchmark handling", () => {
  it("returns a null latestBenchmark and an empty path when benchmark is null", () => {
    const data = normalizePerformanceData(benchmarkUnavailablePayload());
    const model = buildPerformanceChartModel(data as never) as unknown as Record<string, unknown>;

    // Anti-pin: today latestBenchmark falls back to startEquity, which is why
    // "SPY REBASED" rendered $99,492.94 (exactly the starting equity) live.
    expect(model.latestBenchmark).toBeNull();
    expect(model.benchmarkPath).toBe("");
    expect(model.rebasedBenchmarkValues).toEqual([]);
  });

  it("draws the benchmark line when the aligned series is present", () => {
    const data = normalizePerformanceData(goldenOkPayload());
    const model = buildPerformanceChartModel(data as never) as unknown as Record<string, unknown>;

    expect(typeof model.latestBenchmark).toBe("number");
    // §C.5: the plotted base is twr_index, so the benchmark rebases onto the
    // first plotted value (100), not onto dollar starting equity.
    // 610.58 / 592.11 * 100 = 103.11935282295519
    expect(model.latestBenchmark as number).toBeCloseTo(103.11935282295519, 6);
    expect(model.benchmarkPath).not.toBe("");
  });
});

// ---------------------------------------------------------------------------
// flows-D4 — a LEGACY v1 payload must be refused, never defaulted to healthy
// ---------------------------------------------------------------------------

describe("legacy v1 payload refusal (flows-D4)", () => {
  it("refuses a payload with no status key instead of defaulting it to ok", () => {
    const view = buildPerformanceView(legacyV1LivePayload());
    if (!view) throw new Error("buildPerformanceView returned null for the live v1 row");

    // A v1 row carries no integrity evidence at all: no status, no flows_status,
    // no nav_sessions_behind. "Absent" is not "healthy".
    expect(view.status).not.toBe("ok");
    expect(view.status).not.toBe("stale");
    expect(["degraded", "unavailable"]).toContain(view.status);

    // +951.28% must never surface. It is the chained 2026-02-06 ACATS.
    expect(view.twrCumReturn).toBeNull();
    expect(view.twrCumReturn).not.toBe(LIVE_DEFECT_VALUES.cumReturn);
    expect(view.annualized.value).toBeNull();
    expect(view.errorWarnings.length).toBeGreaterThan(0);
  });

  it("refuses the pre-v2 row that DECLARES status ok, because no gate produced that word", () => {
    // The row `/performance` actually serves. `status: "ok"` written by a
    // writer that ran none of the v2 integrity gates is not evidence of health,
    // and §C.2 Gate 2 puts disk_cache + 105 sessions behind at `degraded`.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-15T14:55:40Z"));
    try {
      const view = buildPerformanceView(legacyV1DeclaredOkPayload());
      if (!view) throw new Error("buildPerformanceView returned null for the live ok-declaring row");

      expect(view.status).not.toBe("ok");
      expect(view.status).not.toBe("stale");
      expect(view.flowsStatus).toBe("failed");
      expect(view.errorWarnings.map((w) => w.code)).toContain("LEGACY_PAYLOAD");
      expect(view.navSessionsBehind).toBeGreaterThan(TWR_GATES.NAV_STALENESS_BUDGET_SESSIONS);

      // F4: the live combination must be unreproducible, including its numbers.
      expect(view.twrCumReturn).toBeNull();
      expect(view.annualized.value).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("agrees with normalizePerformanceData that a declared-ok pre-v2 row is not ok", () => {
    const raw = legacyV1DeclaredOkPayload();
    const normalizedStatus = normalized(raw as unknown as PerformancePayloadV2).status;
    const view = buildPerformanceView(raw);
    if (!view) throw new Error("buildPerformanceView returned null for the live ok-declaring row");

    expect(normalizedStatus).toBe(view.status);
    expect(normalizedStatus).not.toBe("ok");
  });

  it("makes normalizePerformanceData and buildPerformanceView agree on the flows_status default", () => {
    // Today normalizeV2 defaults the field to "failed" (performanceData.ts:271)
    // while buildPerformanceView defaults it to "" (performanceData.ts:545), and
    // the UI consumes the permissive one, so the ""-path never suppresses.
    const raw = legacyV1LivePayload();
    const fromNormalizer = normalized(raw as unknown as PerformancePayloadV2).flows_status;
    const view = buildPerformanceView(raw);
    if (!view) throw new Error("buildPerformanceView returned null for the live v1 row");

    // The agreed default is the conservative one: unobserved flows are failed.
    expect(view.flowsStatus).toBe("failed");
    expect(view.flowsStatus).toBe(fromNormalizer);
  });

  it("derives nav_sessions_behind from nav_as_of rather than defaulting to 0", () => {
    // nav_as_of 2026-03-20 (Fri), evaluated 2026-08-15 (Sat).
    // Weekdays 2026-03-23 .. 2026-08-14 = 9 + 30 + 31 + 30 + 31 + 14 = 145 days
    //   = 20 whole weeks (100 weekdays, through Sun 2026-08-09) + Mon-Fri 08-10..08-14
    //   = 105 weekdays, less the 4 closed sessions in the window
    //     (Good Friday 04-03, Memorial Day 05-25, Juneteenth 06-19, July 4 obs. 07-03)
    //   = 101 completed sessions.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-15T14:55:40Z"));
    try {
      const view = buildPerformanceView(legacyV1LivePayload());
      if (!view) throw new Error("buildPerformanceView returned null for the live v1 row");

      expect(view.navSessionsBehind).not.toBe(0);
      expect(view.navSessionsBehind).toBeGreaterThan(TWR_GATES.NAV_STALENESS_BUDGET_SESSIONS);
      // 101 sessions, or 105 if the implementation counts weekdays without the
      // holiday calendar. Anything outside that band is not a derivation.
      expect(view.navSessionsBehind).toBeGreaterThanOrEqual(101);
      expect(view.navSessionsBehind).toBeLessThanOrEqual(105);
    } finally {
      vi.useRealTimers();
    }
  });
});

// ---------------------------------------------------------------------------
// math-D3 — the chart plots one comparable base, not dollars vs a rebase
// ---------------------------------------------------------------------------

describe("buildPerformanceChartModel base (math-D3)", () => {
  it("plots the TWR index, so a pure deposit cannot read as outperformance", () => {
    // The whole NAV doubling is a +100,000 flow; the period's TWR is 0.0.
    const data = normalizePerformanceData(flowDoubledNavZeroTwrPayload());
    const model = buildPerformanceChartModel(data as never) as unknown as Record<string, unknown>;

    // twr_index base 100, cum_return 0.0 -> 100 * (1 + 0.0) = 100.
    expect(model.latestEquity).toBe(100);
    // Benchmark rebased onto the SAME base: 111.00 / 100.00 * 100 = 111.00.
    expect(model.latestBenchmark as number).toBeCloseTo(111, 10);

    // Anti-pin: dollar NAV against a dollar-rebased SPY draws 200,000 vs 111,000.
    expect(model.latestEquity).not.toBe(200000);
    expect(model.rebasedBenchmarkValues as number[]).not.toContain(111000);
  });

  it("keeps both lines on one base so their percentages are comparable", () => {
    const data = normalizePerformanceData(flowDoubledNavZeroTwrPayload());
    const model = buildPerformanceChartModel(data as never);
    const benchmarkValues = model.rebasedBenchmarkValues;
    const firstPlotted = (data as unknown as { series: Array<{ equity: number }> }).series[0].equity;

    // Portfolio: 0.00% over the window. SPY: 111/100 - 1 = 11.00%.
    expect(model.latestEquity / firstPlotted - 1).toBeCloseTo(0, 12);
    expect((model.latestBenchmark as number) / benchmarkValues[0] - 1).toBeCloseTo(0.11, 12);
  });
});

// ---------------------------------------------------------------------------
// §C.1 / §C.2 — degradation is never silent
// ---------------------------------------------------------------------------

describe("status and degradation semantics", () => {
  it("a failed flow fetch is degraded, has no TWR, and carries a warning", () => {
    const out = normalized(flowsFailedDegradedPayload());

    expect(out.status).toBe("degraded");
    expect(out.flows_status).toBe("failed");
    expect((out.twr as Record<string, unknown>).cum_return).toBeNull();
    expect((out.warnings as unknown[]).length).toBeGreaterThan(0);
  });

  it("no payload is ever status ok with failed flows (F3)", () => {
    const payloads = [
      goldenOkPayload(),
      flowsFailedDegradedPayload(),
      staleDiskCachePayload(),
      suspectSubperiodDegradedPayload(),
      insufficientDataPayload(),
      unavailablePayload(),
      livePathologicalPayload(),
    ];
    for (const payload of payloads) {
      const out = normalized(payload);
      if (out.status === "ok") {
        expect(out.flows_status, `status ok with flows_status ${String(out.flows_status)}`).not.toBe("failed");
      }
    }
  });

  it("no payload is ever status ok on a non-live or stale NAV source (F4)", () => {
    for (const payload of [goldenOkPayload(), staleDiskCachePayload(), livePathologicalPayload()]) {
      const out = normalized(payload);
      if (out.status === "ok") {
        expect(out.nav_source).toBe("flex_live");
        expect(out.nav_sessions_behind as number).toBeLessThanOrEqual(2);
      }
    }
  });

  it("keeps the quarantined subperiod visible with its skip reason", () => {
    const out = normalized(suspectSubperiodDegradedPayload());
    const subperiods = out.subperiods as Array<Record<string, unknown>>;

    expect(out.status).toBe("degraded");
    expect((out.counts as Record<string, number>).n_suspect).toBe(1);
    expect(subperiods[0].skip_reason).toBe("suspect_no_flow");
    expect(subperiods[0].r).toBeNull();
    // Anti-pin: the chained value that produced +951.28% total.
    expect(subperiods[0].r).not.toBe(LIVE_DEFECT_VALUES.suspectSubperiodReturn);
  });

  it("preserves an unavailable payload's shape rather than returning null", () => {
    const out = normalized(unavailablePayload());

    expect(out.status).toBe("unavailable");
    expect(out.nav_source).toBe("none");
    expect(out.series).toEqual([]);
    expect((out.warnings as Array<Record<string, unknown>>)[0].code).toBe("NAV_UNAVAILABLE");
  });
});

// ---------------------------------------------------------------------------
// §C.6 / §E.9 tests 86-87 — the route
// ---------------------------------------------------------------------------

const mockReadFile = vi.fn();
const mockStat = vi.fn();
vi.mock("fs/promises", () => ({ readFile: mockReadFile, stat: mockStat }));

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

const mockGetDb = vi.fn();
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: mockGetDb }));

function mockPerformanceRow(payload: Record<string, unknown> | null, takenAt: string): void {
  mockGetDb.mockReturnValue({
    execute: vi.fn(async ({ sql }: { sql: string }) => {
      if (/FROM\s+performance_snapshots/i.test(sql)) {
        return { rows: payload ? [{ taken_at: takenAt, payload: JSON.stringify(payload) }] : [] };
      }
      return { rows: [] };
    }),
  });
}

describe("/api/performance v2 route contract", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-15T14:56:40Z"));
    vi.clearAllMocks();
    mockReadFile.mockReset();
    mockStat.mockReset();
    mockRadonFetch.mockReset();
    mockGetDb.mockReset();
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    mockStat.mockRejectedValue(new Error("ENOENT"));
    // Default so a fire-and-forget background rebuild resolves instead of
    // throwing inside the route; individual tests override it.
    mockRadonFetch.mockResolvedValue({ status: "accepted" });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const statusCases: Array<[string, () => PerformancePayloadV2]> = [
    ["degraded", flowsFailedDegradedPayload],
    ["stale", staleDiskCachePayload],
    ["insufficient_data", insufficientDataPayload],
    ["unavailable", unavailablePayload],
  ];

  for (const [label, build] of statusCases) {
    it(`serves HTTP 200 with status "${label}" intact and unrewritten`, async () => {
      const payload = build();
      mockPerformanceRow(payload as unknown as Record<string, unknown>, payload.generated_at);

      const { GET } = await import("../app/api/performance/route");
      const res = await GET();
      const body = await res.json();

      expect(res.status).toBe(200);
      expect(body.status).toBe(label);
      expect(body.warnings).toEqual(payload.warnings);
      expect(body.nav_source).toBe(payload.nav_source);
      expect(body.flows_status).toBe(payload.flows_status);
      expect(body).toEqual(JSON.parse(JSON.stringify(payload)));
    });
  }

  it("times freshness off the full-ISO generated_at, not a date-only taken_at", async () => {
    // Row key is date-only (the legacy writer shape); the payload was generated
    // 60s ago. A date-only key makes every row permanently stale, which is what
    // kept /performance triggering rebuilds against a 5-month-old snapshot.
    const payload = goldenOkPayload();
    payload.generated_at = "2026-08-15T14:55:40.320113Z";
    mockPerformanceRow(payload as unknown as Record<string, unknown>, "2026-08-15");

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.generated_at).toBe("2026-08-15T14:55:40.320113Z");
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("returns 200 with status unavailable when there is no snapshot and the builder fails", async () => {
    // §C.6: missing or degraded data is never a 4xx and never a 5xx.
    mockPerformanceRow(null, "");
    mockRadonFetch.mockRejectedValue(new Error("FastAPI down"));

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.status).toBe("unavailable");
    expect(Array.isArray(body.warnings)).toBe(true);
    expect(body.warnings.map((w: { code: string }) => w.code)).toContain("NAV_UNAVAILABLE");
    expect(JSON.stringify(body)).not.toContain("FastAPI down");
  });
});
