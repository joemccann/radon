/**
 * @vitest-environment jsdom
 *
 * REL-092 — a panel must be able to say "this failed".
 *
 * These cases used to read the component SOURCE TEXT and regex it. That is
 * net-negative: `expect(src).toMatch(/coneMissing/)` is satisfied by a variable
 * that is assigned and never rendered, and
 * `/GammaRotationBody data=\{data\}[^>]*refreshFailed=\{error\}/` pins the
 * current PROP ORDER — a harmless reorder went red while a `GammaRotationBody`
 * that accepts `refreshFailed` and renders nothing for it stayed green. Every
 * case below now MOUNTS the component with a stubbed sync hook and asserts on
 * the DOM, so the assertion is the behaviour R-245/246/247/272/273 exist for.
 *
 * R-245: the vol-cone GET contract is HTTP 200 with `missing: true`
 * (`web/lib/volCone.ts`). `ScannerHero` never read it — the cone branch tested
 * loading, error, then `!hits?.length` — so a total outage painted as a
 * completed scan that found nothing, and the meta rail reinforced it because
 * `?? null` only fires on `undefined`, leaving `scanned`/`candidates` at 0
 * rather than "—".
 *
 * R-246: `VolConePanel` never destructured `error`, though `UseSyncReturn`
 * exposes it. A permanently failing route left `data` null forever and the
 * component fell into an empty state reading "Data appears after the first
 * successful pull" — a fetch fault rendered verbatim as a benign
 * pre-population state, with no path that could display an error at all.
 *
 * R-247: `if (error && !data)` was the only consumer of `error`, so once a
 * payload had ever loaded a sustained failure left the full populated panel on
 * screen with a freshness badge derived entirely from the stale cached
 * payload. Same shape in `OptionsExposurePanel`.
 *
 * R-272: `coneFillPct` returned 0 both for "cone bounds unavailable" and for
 * "sitting at or above the p90 ceiling" — and the bar reads longer-is-better,
 * so an uncomputable cone rendered as maximally rich.
 *
 * R-273: `finiteBrushValue` mapped a null `atm_iv` to 0 and fed the brush,
 * plotting a missing session as a 0% implied-vol floor spike — while the type
 * comment says "Nulls are preserved for chart gaps" and the main chart honours
 * that.
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { VolConeData, VolConeName, VolConeSeriesPoint } from "@/lib/volCone";
import { coneFillPct } from "../lib/scannerHero";
import { EXPOSURE_FIXTURE } from "./options-exposure-fixture";

/* ─── Stubbed sync hooks ──────────────────────────────────────── */

type HookState<T> = {
  data: T | null;
  loading: boolean;
  syncing: boolean;
  error: string | null;
  lastSync: string | null;
  syncNow: () => void;
};

function idle<T>(partial: Partial<HookState<T>> = {}): HookState<T> {
  return {
    data: null,
    loading: false,
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: vi.fn(),
    ...partial,
  };
}

const mockUseVolCone = vi.fn(() => idle<VolConeData>());
const mockUseThetaHarvester = vi.fn(() => idle<unknown>());
const mockUseGammaRotation = vi.fn(() => idle<unknown>());
const mockUseOptionsExposure = vi.fn(() => ({
  data: EXPOSURE_FIXTURE,
  loading: false,
  error: null as string | null,
  refresh: vi.fn(),
}));

vi.mock("@/lib/useVolCone", () => ({ useVolCone: () => mockUseVolCone() }));
vi.mock("@/lib/useThetaHarvester", () => ({ useThetaHarvester: () => mockUseThetaHarvester() }));
vi.mock("@/lib/useGammaRotation", () => ({ useGammaRotation: () => mockUseGammaRotation() }));
vi.mock("@/lib/useOptionsExposure", () => ({
  useOptionsExposure: () => mockUseOptionsExposure(),
}));
vi.mock("@/lib/useMarketHours", () => ({
  MarketState: { OPEN: "OPEN", CLOSED: "CLOSED", EXTENDED: "EXTENDED" },
}));
// The badge calls isGammaRotationStale(data) directly. Pinning it to false
// isolates the refreshFailed branch: without it the badge would read LIVE.
vi.mock("@/lib/gammaRotationStaleness", () => ({ isGammaRotationStale: () => false }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

import GammaRotationPanel from "@/components/GammaRotationPanel";
import OptionsExposurePanel from "@/components/OptionsExposurePanel";
import VolConePanel from "@/components/VolConePanel";
import ScannerHero from "@/components/dashboard/ScannerHero";

beforeAll(() => {
  if (typeof globalThis.ResizeObserver === "undefined") {
    class StubResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as unknown as { ResizeObserver: typeof StubResizeObserver }).ResizeObserver =
      StubResizeObserver;
  }
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/* ─── Fixtures ────────────────────────────────────────────────── */

function conePoint(date: string, atm: number | null): VolConeSeriesPoint {
  return { date, spot: 220, atm_iv: atm, call_10_iv: 0.38, put_10_iv: 0.4 };
}

function coneName(overrides: Partial<VolConeName> = {}): VolConeName {
  return {
    ticker: "NVDA",
    spot: 223.95,
    expiry: "2026-09-18",
    dte: 37,
    atm_iv: 0.385,
    call_10_iv: 0.386,
    put_10_iv: 0.397,
    call_10_strike: 246.345,
    put_10_strike: 201.555,
    p10: 0.3879,
    p90: 0.443,
    atm_percentile: 0,
    call_10_percentile: 0.0556,
    put_10_percentile: 0.1111,
    wing_score: 0.0833,
    regime: "CHEAP_WINGS",
    series: [
      conePoint("2026-08-10", 0.4),
      conePoint("2026-08-11", 0.41),
      conePoint("2026-08-12", 0.39),
    ],
    ...overrides,
  };
}

function coneData(overrides: Partial<VolConeData> = {}): VolConeData {
  const nvda = coneName();
  return {
    scan_time: "2026-08-12T20:45:00Z",
    source_as_of: "2026-08-12",
    count: 118,
    hit_count: 1,
    current: nvda,
    names: [nvda],
    hits: [nvda],
    ...overrides,
  };
}

/** The settled outage payload: HTTP 200, no error, `missing: true`. */
const CONE_MISSING: VolConeData = {
  missing: true,
  scan_time: null,
  source_as_of: null,
  count: 0,
  hit_count: 0,
  current: null,
  names: [],
  hits: [],
};

function makeGrgData() {
  return {
    scan_time: "2026-05-31T15:00:00Z",
    market_open: true,
    data_date: "2026-05-29",
    source: "Unusual Whales",
    storage: "turso",
    lookback_days: 250,
    z_window: 63,
    signal: {
      state: "RISK_ON_DIVERGENCE",
      state_label: "Risk-on divergence",
      interpretation: "TOP_WATCH",
      tier: 2,
      top_watch: true,
      bottom_watch: false,
      top_score: 4,
      bottom_score: 1,
      grg_z: 2.68,
      raw_spread: 3.12,
      spy_gamma_z: 1.84,
      tlt_gamma_z: -1.67,
      spy_3d_gamma_change: -1000,
      tlt_3d_gamma_change: -500,
      summary: "SPY gamma cushioning equities while TLT gamma amplifies duration moves.",
    },
    assets: {
      SPY: {
        ticker: "SPY", spot: 590, data_date: "2026-05-29", strike_data_date: "2026-05-29",
        net_gamma: 836147.5, net_gex: 836147.5, call_gex: 4047846.7, put_gex: -3211699.2,
        net_delta: 177651415, gamma_z: 1.84, gamma_1d_change: 2000, gamma_3d_change: -1000,
        state: "CUSHION", spot_vs_flip_pct: 1.2,
        levels: { gex_flip: { strike: 583, gamma: 0, distance: -7, distance_pct: -1.2 } },
      },
      TLT: {
        ticker: "TLT", spot: 91, data_date: "2026-05-29", strike_data_date: "2026-05-29",
        net_gamma: -721000, net_gex: -721000, call_gex: 100, put_gex: -721100,
        net_delta: 123, gamma_z: -1.67, gamma_1d_change: -2000, gamma_3d_change: -500,
        state: "WHIP", spot_vs_flip_pct: -0.8,
        levels: { gex_flip: { strike: 92, gamma: 0, distance: 1, distance_pct: 1.1 } },
      },
    },
    gates: [{ id: "polarity", label: "Polarity", status: "PASS", copy: "Clean risk-on divergence." }],
    history: [
      { date: "2026-05-29", spy_net_gamma: 3, tlt_net_gamma: -3, spy_gamma_z: 1.84, tlt_gamma_z: -1.67, grg_z: 2.68, raw_spread: 3.51, state: "RISK_ON_DIVERGENCE" },
    ],
    top_bottom: {
      top: { active: true, copy: "Potential top copy." },
      bottom: { active: false, copy: "Potential bottom copy." },
    },
  };
}

function openConeTab() {
  fireEvent.click(screen.getByRole("button", { name: /vol cone/i }));
}

/* ─── Cases ───────────────────────────────────────────────────── */

describe("ScannerHero reads the missing flag", () => {
  it("renders an outage alert for a settled missing payload", () => {
    mockUseVolCone.mockReturnValue(idle({ data: CONE_MISSING }));
    const { container } = render(<ScannerHero />);
    openConeTab();

    expect(container.textContent).toContain("Vol cone data unavailable");
    expect(screen.getByRole("alert").textContent).toContain("outage, not an empty result");
    // The benign "scan found nothing" copy must NOT be what an outage renders.
    expect(screen.queryByText(/No cheap vol cones/)).toBeNull();
  });

  it("does not report a scanned/candidate count for a missing payload", () => {
    mockUseVolCone.mockReturnValue(idle({ data: CONE_MISSING }));
    const { container } = render(<ScannerHero />);
    openConeTab();

    const rail = container.querySelector(".panel-meta-rail")!;
    const cell = (key: string) =>
      Array.from(rail.querySelectorAll(".panel-meta-rail-item"))
        .find((item) => item.querySelector(".k")?.textContent === key)
        ?.querySelector(".v")?.textContent;
    expect(cell("scanned")).toBe("—");
    expect(cell("candidates")).toBe("—");
    expect(cell("scanned")).not.toBe("0");
  });
});

describe("VolConePanel can render a fault", () => {
  it("renders a fault state instead of the pre-population copy when the fetch fails", () => {
    mockUseVolCone.mockReturnValue(idle<VolConeData>({ error: "fetch failed" }));
    const { container } = render(<VolConePanel />);

    expect(container.textContent).toContain("Vol cone unavailable");
    expect(container.textContent).not.toContain("Data appears after the first successful pull");
  });

  it("puts the failure reason itself in the DOM", () => {
    mockUseVolCone.mockReturnValue(idle<VolConeData>({ error: "fetch failed" }));
    const { container } = render(<VolConePanel />);

    expect(container.textContent).toContain("fetch failed");
  });
});

describe("Panels surface a failed refresh behind a cached payload", () => {
  it("GammaRotationPanel badges a failed refresh as stale, not live", () => {
    mockUseGammaRotation.mockReturnValue(
      idle({ data: makeGrgData(), lastSync: "2026-05-31T15:00:00Z", error: "fetch failed" }),
    );
    const { container } = render(<GammaRotationPanel />);

    // The cached payload is still on screen ...
    expect(container.textContent).toContain("Gamma Rotation Gap");
    // ... but the badge must not read as fresh. market_open is true and
    // isGammaRotationStale is false, so LIVE is what a dropped error yields.
    const badge = container.querySelector("[data-testid='grg-freshness-badge']")!;
    expect(badge.getAttribute("data-state")).toBe("stale");
    expect(badge.textContent?.trim()).toBe("STALE");
    expect(badge.className).toContain("grg-status-badge-stale");
  });

  it("OptionsExposurePanel does not drop error once data exists", () => {
    mockUseOptionsExposure.mockReturnValue({
      data: EXPOSURE_FIXTURE,
      loading: false,
      error: "fetch failed",
      refresh: vi.fn(),
    });
    const { container } = render(<OptionsExposurePanel symbol="MU" />);

    // The last good ladder is still rendered ...
    expect(container.querySelector("[data-testid='options-exposure-panel']")).toBeTruthy();
    expect(screen.getByRole("table", { name: /options exposure by strike/i })).toBeTruthy();
    // ... and it is labelled as stale, carrying the reason.
    const flag = screen.getByText("REFRESH FAILED");
    expect(flag.getAttribute("title")).toBe("fetch failed");
    expect(flag.getAttribute("role")).toBe("status");
  });
});

describe("coneFillPct distinguishes unavailable from rich", () => {
  it("returns null when the cone bounds cannot be computed", () => {
    expect(coneFillPct({ atm_iv: 20, p10: null, p90: null })).toBeNull();
    expect(coneFillPct({ atm_iv: null, p10: 10, p90: 30 })).toBeNull();
    // Degenerate cone.
    expect(coneFillPct({ atm_iv: 20, p10: 30, p90: 30 })).toBeNull();
  });

  it("still returns 0 for a name at the p90 ceiling", () => {
    expect(coneFillPct({ atm_iv: 30, p10: 10, p90: 30 })).toBe(0);
  });

  it("returns 100 on the cone floor", () => {
    expect(coneFillPct({ atm_iv: 10, p10: 10, p90: 30 })).toBe(100);
  });
});

describe("the brush minimap keeps gaps as gaps", () => {
  it("does not floor a null atm_iv to zero", () => {
    const gapped = coneName({
      series: [
        conePoint("2026-08-10", 0.4),
        conePoint("2026-08-11", null),
        conePoint("2026-08-12", 0.39),
      ],
    });
    mockUseVolCone.mockReturnValue(
      idle({ data: coneData({ current: gapped, names: [gapped], hits: [gapped] }) }),
    );
    const { container } = render(<VolConePanel />);

    const line = container.querySelector("[data-testid='vol-cone-brush'] .brush-minimap-line")!;
    const d = line.getAttribute("d") ?? "";
    expect(d).not.toBe("");
    // d3's .defined() opens a NEW subpath across a gap. A null floored to 0
    // would join the sessions into one continuous crash-low spike instead.
    expect(d.match(/M/g)?.length ?? 0).toBeGreaterThan(1);
  });
});
