/**
 * @vitest-environment jsdom
 *
 * Residual web-side defects R6, R8, R9 and the two minors, on screen.
 *
 * R6 — a v2 payload frozen on 2026-03-27 renders a confident "+5.01%" hero with
 *      no banner when it is read on 2026-08-15, 96 completed sessions later.
 * R8 — flowsFailedDegradedPayload() now carries the REAL builder output, which
 *      pairs a measured ending equity with counts.n_returns 60 on a branch that
 *      chained zero returns (series [] / subperiods []). The panel prints
 *      "Ending equity $182,217.36 / 2026-03-27 / N=60".
 * R9 — Python suppresses a benchmark to `benchmark: null` and puts the real
 *      reason in a BENCHMARK_UNAVAILABLE warning context. The reader maps
 *      `!block` to hasBenchmarkSeries=false and both panels delete the cards,
 *      so benchmark_coverage / benchmark_degenerate / benchmark insufficient_n
 *      copy is unreachable on screen.
 * Minor — the methodology footer prints "3.74% (fallback 0)" while the Sharpe
 *      card prints "RF 3.74% DGS3MO" off the SAME payload.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockUsePerformance = vi.fn();
vi.mock("@/lib/usePerformance", () => ({
  usePerformance: (...args: unknown[]) => mockUsePerformance(...args),
}));

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({
    viewportClass: "desktop",
    isMobile: false,
    isTablet: false,
    isDesktop: true,
    width: 1280,
    hasMounted: true,
  }),
}));

import PerformancePanel from "../components/PerformancePanel";
import MobilePerformancePanel from "../components/mobile/MobilePerformancePanel";
import { normalizePerformanceData } from "../lib/performanceData";
import {
  benchmarkCoverageSuppressedPayload,
  benchmarkDegenerateSuppressedPayload,
  benchmarkInsufficientNPayload,
  flowsFailedDegradedPayload,
  goldenOkPayload,
  type PerformancePayloadV2,
} from "./fixtures/performanceScenarios";

function stubV2(payload: unknown): void {
  mockUsePerformance.mockReturnValue({
    data: normalizePerformanceData(payload),
    loading: false,
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
  });
}

afterEach(() => {
  cleanup();
  mockUsePerformance.mockReset();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// R6 — a frozen payload publishes confidently five months later
// ---------------------------------------------------------------------------

/** Saturday 2026-08-15 10:55 ET. Last completed session: Fri 2026-08-14.
 *  SESSIONS strictly after nav_as_of 2026-03-27 through 2026-08-14:
 *    weekdays Mar 30-31 (2) + Apr (22) + May (21) + Jun (22) + Jul (23)
 *             + Aug 3-14 (10) = 100
 *    less the four full-closure holidays in the window (2026-04-03, 05-25,
 *    06-19, 07-03) = 96, far past NAV_STALENESS_BUDGET_SESSIONS (2).
 *  Python agrees: `sessions_behind("2026-03-27", date(2026,8,14)) == 96`. */
const READ_INSTANT = "2026-08-15T14:55:40Z";
const DERIVED_SESSIONS_BEHIND = "96";

describe("R6 — a v2 payload written 2026-03-27, read 2026-08-15", () => {
  const variants: Array<[string, () => PerformancePayloadV2]> = [
    ["nav_sessions_behind declared 0", () => goldenOkPayload()],
    [
      "nav_sessions_behind absent entirely",
      () => {
        const payload = goldenOkPayload();
        delete (payload as unknown as Record<string, unknown>).nav_sessions_behind;
        return payload;
      },
    ],
  ];

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(READ_INSTANT));
  });

  for (const [label, build] of variants) {
    it(`desktop: ${label} renders a stale banner and a suppressed hero`, () => {
      stubV2(build());
      render(<PerformancePanel />);

      const banner = screen.getByTestId("performance-stale-banner");
      expect(banner.textContent).toContain("2026-03-27");
      expect(banner.textContent).toContain(DERIVED_SESSIONS_BEHIND);
      // cum_return 0.050112307160331326 -> "+5.01%" is what ships today.
      expect(screen.getByTestId("performance-hero-twr").textContent ?? "").not.toMatch(/\d/);
      expect(document.body.textContent ?? "").not.toContain("+5.01%");
    });

    it(`mobile: ${label} renders a stale banner and a suppressed hero`, () => {
      stubV2(build());
      render(<MobilePerformancePanel />);

      expect(screen.getByTestId("performance-stale-banner")).toBeTruthy();
      expect(screen.getByTestId("performance-hero-twr").textContent ?? "").not.toMatch(/\d/);
      expect(document.body.textContent ?? "").not.toContain("+5.01%");
    });
  }
});

// ---------------------------------------------------------------------------
// R8 — a false count rendered as a measured sample
// ---------------------------------------------------------------------------

describe("R8 — the suppressed branch must not publish a count it never computed", () => {
  it("the fixture IS the builder's real output for the zero-return branch", () => {
    // Guard on the fixture itself: R8 exists because the previous literals
    // claimed calendar_days 0 / equity {} / n_returns 0 while the builder emits
    // 84 / real endpoints / 60. If someone "fixes" this by editing the fixture
    // back, the defect goes invisible again.
    const payload = flowsFailedDegradedPayload();
    expect(payload.calendar_days).toBe(84);
    expect(payload.equity.ending).toBe(182217.35574011656);
    expect(payload.series).toHaveLength(0);
    expect(payload.subperiods).toHaveLength(0);
    expect(payload.risk.volatility.n).toBe(60);
  });

  it("counts.n_returns is 0 when nothing was chained", () => {
    // The builder chained ZERO returns here (series [] / subperiods []), so a
    // return count of 60 is a number it did not compute.
    const payload = flowsFailedDegradedPayload();
    expect(payload.counts.n_subperiods).toBe(60);
    expect(payload.counts.n_returns).toBe(0);
  });

  it("desktop hero never reads 'Ending equity $182,217.36 / ... / N=60'", () => {
    stubV2(flowsFailedDegradedPayload());
    render(<PerformancePanel />);

    const subtitle = screen.getByTestId("performance-hero-subtitle").textContent ?? "";
    expect(subtitle).not.toContain("N=60");
    expect(subtitle).toContain("N=--");
    expect(subtitle).not.toContain("$0.00");
    expect(screen.getByTestId("performance-degraded-banner")).toBeTruthy();
  });

  it("mobile hero never reads N=60 on a branch that chained nothing", () => {
    stubV2(flowsFailedDegradedPayload());
    render(<MobilePerformancePanel />);

    const subtitle = screen.getByTestId("performance-hero-subtitle").textContent ?? "";
    expect(subtitle).not.toContain("N=60");
    expect(subtitle).toContain("N=--");
  });
});

// ---------------------------------------------------------------------------
// R9 — benchmark reasons must reach the screen
// ---------------------------------------------------------------------------

const BENCHMARK_CARDS = ["beta", "alpha", "tracking-error"] as const;

/**
 * Each case is a payload the Python builder can actually emit: `benchmark:
 * null` plus one BENCHMARK_UNAVAILABLE warning carrying the reason. The reader
 * must lift that reason out of the warning context so `gateCopy` can word it.
 *
 *   insufficient_n      -> `needs ${MIN_N_BENCHMARK} sessions (N=${n_common})`
 *   benchmark_coverage  -> `SPY covers ${round(coverage*100)}% of sessions`
 *   benchmark_degenerate-> "SPY series has no variance"
 */
const BENCHMARK_REASON_CASES: Array<[reason: string, build: () => PerformancePayloadV2, copy: string]> = [
  ["insufficient_n", benchmarkInsufficientNPayload, "needs 40 sessions (N=30)"],
  ["benchmark_coverage", benchmarkCoverageSuppressedPayload, "SPY covers 83% of sessions"],
  ["benchmark_degenerate", benchmarkDegenerateSuppressedPayload, "SPY series has no variance"],
];

describe("R9 — a suppressed benchmark renders '--' plus its reason, never a deleted card", () => {
  for (const [reason, build, copy] of BENCHMARK_REASON_CASES) {
    it(`desktop: ${reason} renders "${copy}" on beta, alpha and tracking error`, () => {
      stubV2(build());
      render(<PerformancePanel />);

      for (const card of BENCHMARK_CARDS) {
        expect(screen.queryByTestId(`performance-card-${card}`), card).not.toBeNull();
        expect(screen.getByTestId(`performance-value-${card}`).textContent, card).toBe("--");
        expect(screen.getByTestId(`performance-sub-${card}`).textContent, card).toBe(copy);
      }
    });

    it(`mobile: ${reason} renders "${copy}" on beta, alpha and tracking error`, () => {
      stubV2(build());
      render(<MobilePerformancePanel />);

      for (const card of BENCHMARK_CARDS) {
        expect(screen.queryByTestId(`performance-card-${card}`), card).not.toBeNull();
        expect(screen.getByTestId(`performance-value-${card}`).textContent, card).toBe("--");
        expect(screen.getByTestId(`performance-sub-${card}`).textContent, card).toBe(copy);
      }
    });
  }

  it("still publishes no benchmark statistic or rebased figure alongside the reason", () => {
    stubV2(benchmarkDegenerateSuppressedPayload());
    render(<PerformancePanel />);

    const body = document.body.textContent ?? "";
    // goldenOkPayload's benchmark block: beta 0.84, benchmark_return 0.0312.
    expect(body).not.toContain("0.84");
    expect(body).not.toContain("+3.12%");
    expect(screen.queryByText(/SPY REBASED/i)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Minor — the risk-free line contradicts itself
// ---------------------------------------------------------------------------

/** goldenOkPayload with the live 3.74% rate and NO risk_free_source, which is
 *  what every pre-v2 methodology block carries. */
function rfPayload(overrides: Record<string, unknown>): PerformancePayloadV2 {
  const payload = goldenOkPayload();
  payload.methodology = { ...payload.methodology, ...overrides };
  return payload;
}

describe("Minor — risk-free copy must not contradict the rate next to it", () => {
  it("does not print '(fallback 0)' beside a 3.74% rate when the source is merely absent", () => {
    const payload = rfPayload({ risk_free_rate: 0.0374, risk_free_source: undefined });
    delete (payload.methodology as Record<string, unknown>).risk_free_source;
    stubV2(payload);
    render(<PerformancePanel />);

    const footer = screen.getByTestId("methodology-rf").textContent ?? "";
    expect(footer).toContain("3.74%");
    expect(footer.toLowerCase()).not.toContain("fallback");
  });

  it("agrees with the Sharpe card's own risk-free label on the same payload", () => {
    const payload = rfPayload({ risk_free_rate: 0.0374, risk_free_source: undefined });
    delete (payload.methodology as Record<string, unknown>).risk_free_source;
    stubV2(payload);
    render(<PerformancePanel />);

    const footer = screen.getByTestId("methodology-rf").textContent ?? "";
    const sharpeSub = screen.getByTestId("performance-sub-sharpe-vs-tbill").textContent ?? "";
    expect(sharpeSub).toContain("3.74%");
    // Today: footer "3.74% (fallback 0)" vs sharpe "RF 3.74% DGS3MO".
    const footerClaimsFallback = footer.toLowerCase().includes("fallback");
    const sharpeClaimsFred = sharpeSub.toUpperCase().includes("DGS3MO");
    expect(footerClaimsFallback && sharpeClaimsFred).toBe(false);
  });

  it("still says fallback when the rate really is the zero fallback", () => {
    // Anti-pin: the fix must not delete the honest fallback wording.
    stubV2(rfPayload({ risk_free_rate: 0, risk_free_source: "fallback_zero" }));
    render(<PerformancePanel />);

    const footer = screen.getByTestId("methodology-rf").textContent ?? "";
    expect(footer.toLowerCase()).toContain("fallback");
  });

  it("says FRED DGS3MO when the source really is FRED", () => {
    stubV2(rfPayload({ risk_free_rate: 0.0374, risk_free_source: "fred_dgs3mo" }));
    render(<PerformancePanel />);

    expect(screen.getByTestId("methodology-rf").textContent ?? "").toContain("FRED DGS3MO");
  });
});
