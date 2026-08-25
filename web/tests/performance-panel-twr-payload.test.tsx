/**
 * @vitest-environment jsdom
 *
 * Production /performance crash: Turso serves the TWR builder snapshot, which
 * has no contracts_missing_history / trades_source / price_sources. The panel
 * then throws TypeError: Cannot read properties of undefined (reading 'length').
 */
import { existsSync, readFileSync } from "fs";
import { resolve } from "path";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mockUsePerformance = vi.fn();
vi.mock("@/lib/usePerformance", () => ({
  usePerformance: (...args: unknown[]) => mockUsePerformance(...args),
}));

// §E.9 test 79 — normalization happens ONCE, in the hook. A spy that delegates
// to the real implementation lets the panels render while still failing the
// call-count assertion if either panel re-normalizes.
const normalizeSpy = vi.hoisted(() => vi.fn());
vi.mock("@/lib/performanceData", async () => {
  const actual = await vi.importActual<typeof import("../lib/performanceData")>("../lib/performanceData");
  normalizeSpy.mockImplementation(actual.normalizePerformanceData);
  return { ...actual, normalizePerformanceData: (raw: unknown) => normalizeSpy(raw) };
});

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
import { gateCopy, TWR_GATES } from "../lib/performanceTwr";
import {
  benchmarkCoverageShortfallPayload,
  benchmarkUnavailablePayload,
  flowsFailedDegradedPayload,
  goldenOkPayload,
  legacyV1DeclaredOkPayload,
  legacyV1LivePayload,
  livePathologicalPayload,
  staleDiskCachePayload,
  LIVE_DEFECT_VALUES,
  type PerformancePayloadV2,
} from "./fixtures/performanceScenarios";

afterEach(() => {
  cleanup();
  mockUsePerformance.mockReset();
  normalizeSpy.mockClear();
  vi.useRealTimers();
});

/**
 * DECISION 5 makes staleness a READ-time property: the reader derives sessions
 * behind from `nav_as_of` against its own clock and suppresses the hero past
 * the budget, whatever the payload's declared status says. A fixture with a
 * hardcoded `nav_as_of` therefore ages out of freshness on its own, so any test
 * that wants to assert a PUBLISHED number has to say which day it is reading
 * on. 2026-03-20 20:45 ET is the timer hour on the fixture's own last session:
 * zero sessions behind, nothing suppressed, and the assertion survives the
 * calendar.
 */
const FIXTURE_READ_INSTANT = "2026-03-20T20:45:00-04:00";

function readingOn(instant: string, body: () => void) {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(instant));
  try {
    body();
  } finally {
    vi.useRealTimers();
  }
}

/** Pre-v2 shape still sitting in performance_snapshots: no reconstruction
 *  arrays AND no schema_version, so no integrity gate ever ran on it. */
function buildTwrSnapshot() {
  return {
    status: "ok",
    methodology: {
      curve_type: "twr_modified_dietz_daily",
      return_basis: "time_weighted",
      risk_free_rate: 0.0412,
      library_strategy: "fred_dgs3mo",
    },
    summary: {
      total_return: 0.1,
      annualized_return: 0.5,
      trading_days: 57,
      period_start: "2026-01-02",
      period_end: "2026-03-20",
      max_drawdown: -0.05,
      sharpe_ratio: 1.2,
      beta: 0.8,
      alpha: 0.02,
      var_95: null,
      cvar_95: null,
    },
    metrics: { tracking_error: 0.12 },
    series: [
      { date: "2026-01-02", nav: 100_000, return: 0, cum_return: 0, equity: 100 },
      { date: "2026-01-05", nav: 105_000, return: 0.05, cum_return: 0.05, equity: 105 },
      { date: "2026-03-20", nav: 110_000, return: 0.047619, cum_return: 0.1, equity: 110 },
    ],
    warnings: [],
    period_start: "2026-01-02",
    period_end: "2026-03-20",
    benchmark: "SPY",
    nav_source: "disk_cache",
  };
}

/** The same "no reconstruction arrays" payload as the writer emits it today:
 *  schema_version 2, so the panel still renders its number. */
function buildV2SnapshotWithoutReconstruction(): PerformancePayloadV2 {
  const golden = goldenOkPayload();
  return {
    ...golden,
    period_start: "2026-01-02",
    period_end: "2026-03-20",
    nav_as_of: "2026-03-20",
    twr: { ...golden.twr, cum_return: 0.1 },
    equity: { ...golden.equity, ending: 110_000 },
    series: [
      { ...golden.series[0], date: "2026-01-02", nav: 100_000, twr_index: 100, cum_return: 0 },
      { ...golden.series[0], date: "2026-03-20", nav: 110_000, twr_index: 110, cum_return: 0.1 },
    ],
    subperiods: [],
  } as PerformancePayloadV2;
}

function stubPerformance(data: unknown) {
  mockUsePerformance.mockReturnValue({
    data,
    loading: false,
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
  });
}

describe("PerformancePanel TWR snapshot", () => {
  it("renders the desktop panel from a v2 payload that omits reconstruction arrays", () => {
    readingOn(FIXTURE_READ_INSTANT, () => {
      stubPerformance(buildV2SnapshotWithoutReconstruction());

      expect(() => render(<PerformancePanel />)).not.toThrow();

      expect(screen.getByTestId("performance-panel")).toBeTruthy();
      expect(screen.getByTestId("performance-hero-twr").textContent).toBe("+10.00%");
      expect(screen.getByTestId("performance-hero-subtitle").textContent).toContain("Ending equity $110,000.00");
    });
  });

  it("suppresses that very same payload once its NAV has aged past the budget", () => {
    // The other half of DECISION 5, and why the test above has to name its
    // clock: nothing about the payload changed, only the day it is read on.
    readingOn("2026-08-15T12:00:00-04:00", () => {
      stubPerformance(buildV2SnapshotWithoutReconstruction());

      expect(() => render(<PerformancePanel />)).not.toThrow();

      expect(screen.getByTestId("performance-hero-twr").textContent).toBe("--");
      expect(screen.getByTestId("performance-stale-banner")).toBeTruthy();
    });
  });

  it("renders a pre-v2 snapshot without crashing, and without publishing its number", () => {
    // Same missing-arrays shape, but with no schema_version: none of the v2
    // integrity gates ran, so the payload's own "ok" is not evidence. The
    // panel must still render (the original crash regression) with the
    // headline suppressed and the degradation stated.
    stubPerformance(buildTwrSnapshot());

    expect(() => render(<PerformancePanel />)).not.toThrow();

    expect(screen.getByTestId("performance-panel")).toBeTruthy();
    expect(screen.getByTestId("performance-hero-twr").textContent).toBe("--");
    expect(screen.getByTestId("performance-degraded-banner")).toBeTruthy();
    expect(screen.getByTestId("performance-source-pill").textContent).toMatch(/IB NAV/i);
  });

  it("draws no curve for the live legacy row, so +951.28% cannot return as a shape", () => {
    // §C.6, `degraded`: the TWR line is drawn only when `twr` is non-null. The
    // live row's own series climbs 99,492.94 -> 1,045,949.48; plotting it next
    // to a `--` hero renders the suppressed number as a 10x rising line and a
    // "$1,045,949.48" chart-meta figure.
    stubPerformance(legacyV1DeclaredOkPayload());

    expect(() => render(<PerformancePanel />)).not.toThrow();

    expect(screen.getByTestId("performance-hero-twr").textContent).toBe("--");
    expect(screen.getByTestId("performance-line-equity").getAttribute("d")).toBe("");
    expect(screen.getByTestId("performance-chart-panel").textContent).not.toContain("1,045,949");
    expect(screen.getByTestId("performance-curve-suppressed")).toBeTruthy();
  });

  it("renders the insufficient-data empty state when warnings and series are absent", () => {
    stubPerformance({
      status: "insufficient_data",
      methodology: {
        curve_type: "twr_modified_dietz_daily",
        return_basis: "time_weighted",
        risk_free_rate: 0,
        library_strategy: "fred_dgs3mo",
      },
      summary: {},
    });

    expect(() => render(<PerformancePanel />)).not.toThrow();
    expect(screen.getByTestId("performance-insufficient")).toBeTruthy();
  });
});

describe("MobilePerformancePanel TWR snapshot", () => {
  it("renders from the same incomplete TWR payload", () => {
    readingOn(FIXTURE_READ_INSTANT, () => {
      stubPerformance(buildV2SnapshotWithoutReconstruction());

      expect(() => render(<MobilePerformancePanel />)).not.toThrow();

      expect(screen.getByTestId("performance-panel")).toBeTruthy();
      expect(screen.getByTestId("performance-hero-twr").textContent).toBe("+10.00%");
      expect(screen.getByTestId("performance-hero-subtitle").textContent).toContain("Ending equity $110,000.00");
    });
  });

  it("renders a pre-v2 snapshot without crashing, and without publishing its number", () => {
    stubPerformance(buildTwrSnapshot());

    expect(() => render(<MobilePerformancePanel />)).not.toThrow();

    expect(screen.getByTestId("performance-panel")).toBeTruthy();
    expect(screen.getByTestId("performance-hero-twr").textContent).toBe("--");
  });

  it("draws no curve for the live legacy row", () => {
    stubPerformance(legacyV1DeclaredOkPayload());

    expect(() => render(<MobilePerformancePanel />)).not.toThrow();

    expect(screen.getByTestId("performance-hero-twr").textContent).toBe("--");
    expect(screen.getByTestId("performance-line-equity").getAttribute("d")).toBe("");
    expect(screen.getByTestId("mobile-chart-meta").textContent).not.toContain("1,045,949");
  });
});

// ===========================================================================
// v2 payload rendering contract (spec §C.6, §D.4, §E.9 tests 79-85).
// RED-first: these pin the behaviour the live 2026-08-15 payload violated.
// ===========================================================================

/** The hook owns normalization (§C.6); panels receive the normalized object. */
function stubV2(payload: PerformancePayloadV2) {
  const data = normalizePerformanceData(payload);
  normalizeSpy.mockClear();
  stubPerformance(data);
  return data;
}

function renderedValueTexts(): string[] {
  return Array.from(document.querySelectorAll<HTMLElement>('[data-testid^="performance-value-"]')).map(
    (node) => node.textContent ?? "",
  );
}

describe("v2 payload — normalization ownership", () => {
  it("neither panel re-normalizes the hook's payload", () => {
    stubV2(goldenOkPayload());
    render(<PerformancePanel />);
    expect(normalizeSpy).not.toHaveBeenCalled();

    cleanup();
    stubV2(goldenOkPayload());
    render(<MobilePerformancePanel />);
    expect(normalizeSpy).not.toHaveBeenCalled();
  });
});

describe("v2 payload — MWR", () => {
  it("renders the MWR value when the sample clears MIN_N_MWR", () => {
    // golden: n_returns 60 >= MIN_N_MWR 20; period_return 0.05020769210515868
    // -> +5.02%. R-163 extended read-time staleness from the hero to every
    // derived statistic, so this assertion needs the same fixture-day read
    // instant the file already defines for exactly this reason.
    readingOn(FIXTURE_READ_INSTANT, () => {
      stubV2(goldenOkPayload());
      render(<PerformancePanel />);

      expect(TWR_GATES.MIN_N_MWR).toBe(20);
      expect(screen.getByTestId("performance-value-mwr-irr").textContent).toBe("+5.02%");
      expect(screen.queryByTestId("performance-gate-mwr-irr")).toBeNull();
    });
  });

  it("blames the real cause, not the sample size, when flows are unavailable", () => {
    // N=57 clears the gate of 20. The live card said "needs 20 sessions (N=57)".
    const payload = flowsFailedDegradedPayload();
    stubV2(payload);
    render(<PerformancePanel />);

    const sub = screen.getByTestId("performance-sub-mwr-irr").textContent ?? "";
    expect(sub).toBe(gateCopy(payload.mwr.period_return, "MWR IRR"));
    expect(sub).toBe("external flows unavailable");
    expect(document.body.textContent ?? "").not.toContain("needs 20 sessions");
  });
});

describe("v2 payload — benchmark coherence", () => {
  it("renders beta AND the benchmark return together when the block is complete", () => {
    readingOn(FIXTURE_READ_INSTANT, () => {
      stubV2(goldenOkPayload());
      render(<PerformancePanel />);

      expect(screen.getByTestId("performance-value-beta").textContent).toBe("0.84");
      // benchmark_return 0.0312 -> +3.12%
      expect(document.body.textContent ?? "").toContain("+3.12%");
    });
  });

  it("renders NO benchmark STATISTIC when benchmark is null, but still states why (R9)", () => {
    // §C.2: no benchmark-derived FIGURE may appear. The cards themselves stay,
    // dashed, carrying the reason out of the BENCHMARK_UNAVAILABLE warning
    // context — deleting them is what made every benchmark gate string
    // unreachable on screen.
    stubV2(benchmarkUnavailablePayload());
    render(<PerformancePanel />);

    expect(screen.queryByTestId("performance-card-beta")).not.toBeNull();
    expect(screen.getByTestId("performance-value-beta").textContent).toBe("--");
    expect(screen.getByTestId("performance-sub-beta").textContent).toBe("SPY series has no variance");
    expect(screen.getByTestId("performance-value-alpha").textContent).toBe("--");
    expect(screen.getByTestId("performance-value-tracking-error").textContent).toBe("--");
    expect(screen.queryByText(/SPY REBASED/i)).toBeNull();
    expect(screen.queryByText(/Benchmark Return/i)).toBeNull();
  });

  it("cannot render a beta while the benchmark return is unavailable", () => {
    stubV2(livePathologicalPayload());
    render(<PerformancePanel />);

    const body = document.body.textContent ?? "";
    const rendersBeta = body.includes(String(LIVE_DEFECT_VALUES.beta));
    expect(rendersBeta).toBe(false);
    // ALPHA +2190.09% and TRACKING ERROR 634.25% came off the same unusable series.
    expect(body).not.toContain("2190.09");
    expect(body).not.toContain("634.25");
  });
});

describe("v2 payload — degraded and stale states", () => {
  it("degraded renders an explicit banner and no confident hero number", () => {
    const payload = flowsFailedDegradedPayload();
    stubV2(payload);
    render(<PerformancePanel />);

    const banner = screen.getByTestId("performance-degraded-banner");
    expect(banner.textContent).toContain(payload.warnings[0].message);
    expect(screen.getByTestId("performance-hero-twr").textContent ?? "").not.toMatch(/\d/);
    // No card may fabricate a zero for a suppressed metric.
    for (const text of renderedValueTexts()) {
      expect(text).not.toBe("0.00%");
      expect(text).not.toBe("+0.00%");
      expect(text).not.toBe("0.00");
    }
  });

  it("stale renders the as-of date and how far behind the NAV is", () => {
    stubV2(staleDiskCachePayload());
    render(<PerformancePanel />);

    const banner = screen.getByTestId("performance-stale-banner");
    expect(banner.textContent).toContain("2026-03-20");
    expect(banner.textContent).toContain("105");
  });

  it("guards an absurd annualized magnitude instead of printing it", () => {
    // Live render: +3,288,954.62% annualized off a +951.28% total.
    stubV2(livePathologicalPayload());
    render(<PerformancePanel />);

    const body = document.body.textContent ?? "";
    expect(body).not.toContain("3,288,954.62");
    expect(body).not.toContain("3288954");
    expect(body).not.toContain("951.28");
    expect(screen.getByTestId("performance-value-annualized-twr").textContent ?? "").not.toMatch(/\d/);
  });

  it("guards an absurd cum_return even when the payload claims to be healthy", () => {
    // The 951.28 half of the test above passes only because the fixture is
    // "degraded". Flip the SAME fixture to a fully healthy claim — status ok,
    // flows ok, live NAV — so nothing but a cum_return plausibility guard can
    // stop the hero. +951.28% over 79 calendar days is a data defect, not a
    // result: the 2026-02-06 ACATS (246,713.50 -> 972,215.53) chained as
    // +294.07% in a single session.
    const payload: PerformancePayloadV2 = {
      ...livePathologicalPayload(),
      status: "ok",
      flows_status: "ok",
      nav_source: "flex_live",
      nav_as_of: "2026-03-20",
      nav_sessions_behind: 0,
    };
    stubV2(payload);
    render(<PerformancePanel />);

    expect(screen.getByTestId("performance-hero-twr").textContent ?? "").not.toContain("951.28");
    expect(document.body.textContent ?? "").not.toContain("951.28");
  });
});

describe("legacy v1 payload on screen (flows-D4)", () => {
  it("renders a degraded banner, not a +951.28% hero", () => {
    // The literal live row: no status, no flows_status, nav_source disk_cache,
    // period_end 2026-03-20, total_return 9.5128. This is what the page shows
    // today, through the NEW read path.
    stubPerformance(normalizePerformanceData(legacyV1LivePayload()));
    normalizeSpy.mockClear();
    render(<PerformancePanel />);

    const body = document.body.textContent ?? "";
    expect(body).not.toContain("951.28");
    expect(body).not.toContain("3,288,954.62");
    expect(screen.queryByTestId("performance-degraded-banner")).not.toBeNull();
    // A legacy payload publishes no confident number anywhere.
    const hero = screen.queryByTestId("performance-hero-twr");
    expect(hero?.textContent ?? "").not.toMatch(/\d/);
  });
});

describe("degraded payload built by the Python builder (tests-D3)", () => {
  it("renders the suppressed branch without inventing an N (R8)", () => {
    // `_suppressed_payload` publishes the window's real NAV endpoints (an
    // observed fact) but chains nothing: series [] / subperiods []. The return
    // count must therefore be absent, never the 60 subperiods it never chained
    // and never a fabricated 0, which both read as a measured sample.
    stubV2(flowsFailedDegradedPayload());
    render(<PerformancePanel />);

    const subtitle = screen.getByTestId("performance-hero-subtitle").textContent ?? "";
    expect(subtitle).not.toContain("$0.00");
    expect(subtitle).not.toContain("N=0");
    expect(subtitle).not.toContain("N=60");
    expect(subtitle).toContain("N=--");
    expect(screen.getByTestId("performance-degraded-banner")).toBeTruthy();
  });
});

describe("benchmark reason on screen (tests-D7)", () => {
  it("renders a dashed benchmark card carrying its reason instead of deleting it", () => {
    // n_common 50 aligned sessions / n_returns 60 portfolio sessions
    //   = 0.8333... < BENCHMARK_MIN_COVERAGE 0.90  -> no statistic publishable.
    stubV2(benchmarkCoverageShortfallPayload());
    render(<PerformancePanel />);

    expect(screen.queryByTestId("performance-card-beta")).not.toBeNull();
    expect(screen.getByTestId("performance-value-beta").textContent).toBe("--");
    expect(screen.getByTestId("performance-sub-beta").textContent).toBe("SPY covers 83% of sessions");
    // The suppressed statistics themselves never appear.
    const body = document.body.textContent ?? "";
    expect(body).not.toContain("0.84");
  });
});

describe("v2 payload — gate copy comes from the table", () => {
  it("every gated card's copy equals gateCopy for its own GatedValue", () => {
    const payload = flowsFailedDegradedPayload();
    stubV2(payload);
    render(<PerformancePanel />);

    const cases: Array<[string, string, { value: number | null; n: number; min_n: number; unavailable_reason: string | null }]> = [
      ["mwr-irr", "MWR IRR", payload.mwr.period_return],
      ["sharpe-vs-tbill", "Sharpe", payload.risk.sharpe_ratio],
      ["max-drawdown", "Max Drawdown", payload.risk.max_drawdown],
      ["var-95", "VaR 95%", payload.risk.var_95],
      ["cvar-95", "CVaR 95%", payload.risk.cvar_95],
    ];
    for (const [id, label, g] of cases) {
      expect(screen.getByTestId(`performance-sub-${id}`).textContent, id).toBe(gateCopy(g, label));
    }
  });

  it("neither panel hardcodes a gate threshold or a gate string (§E.9 test 85)", () => {
    // jsdom rewrites import.meta.url away from file:, so anchor on cwd —
    // vitest runs from either the repo root or web/.
    const readPanel = (relative: string): string => {
      for (const base of [resolve(process.cwd(), "web"), process.cwd()]) {
        const candidate = resolve(base, relative);
        if (existsSync(candidate)) return readFileSync(candidate, "utf-8");
      }
      throw new Error(`panel source not found: ${relative}`);
    };
    const sources = [
      readPanel("components/PerformancePanel.tsx"),
      readPanel("components/mobile/MobilePerformancePanel.tsx"),
    ];
    for (const source of sources) {
      expect(source).not.toMatch(/>=\s*20\b/);
      expect(source).not.toMatch(/>=\s*40\b/);
      expect(source).not.toMatch(/>=\s*60\b/);
      expect(source).not.toContain("sessions (N=");
      // Matches both the literal glyph and the ≥ escape used in the panels.
      expect(source).not.toMatch(/requires N (≥|\\u2265)/);
      expect(source).toContain("gateCopy");
    }
  });
});
