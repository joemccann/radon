import { describe, expect, it } from "vitest";
import {
  conePosition,
  coneFillPct,
  formatScanSample,
  thetaStructLabel,
  volConeExpiryLabel,
  volConeTone,
} from "../lib/scannerHero";
import type { ThetaHarvesterResult } from "../lib/types";
import type { VolConeName } from "../lib/volCone";

function thetaResult(putStrike: number, callStrike: number): ThetaHarvesterResult {
  const leg = (strike: number, right: "C" | "P") => ({
    symbol: "TXN",
    expiry: "20260901",
    strike,
    right,
    iv: 0.3,
    delta: 0.1,
    theta: -0.2,
    gamma: 0.01,
    vega: 0.1,
    volume: 100,
    open_interest: 1000,
  });
  return {
    ticker: "TXN",
    score: 98.7,
    verdict: "THETA_HARVEST",
    structure: {
      expiry: "20260901",
      dte: 23,
      short_put: leg(putStrike, "P"),
      short_call: leg(callStrike, "C"),
      net_delta: 0,
      theta: 41.5,
      gamma: 0,
      vega: 0,
      credit: 6.43,
    },
    spot: 280,
    iv: 0.3,
    hv20: 0.2,
    hv60: 0.2,
    iv_rv_edge: 0.1,
    iv_rv_ratio: 1.5,
    trend_20d_pct: 0,
    range_score: 1,
    dealer_support: "SUPPORT",
    net_gex: null,
    gex_flip: null,
    setup: "",
    gates: {},
    errors: [],
  };
}

function coneName(overrides: Partial<VolConeName> = {}): VolConeName {
  return {
    ticker: "NKE",
    spot: 72.4,
    expiry: "2026-09-18",
    month: "SEP",
    dte: 24,
    atm_iv: 0.28,
    call_10_iv: 0.3,
    put_10_iv: 0.31,
    call_10_strike: 79.6,
    put_10_strike: 65.2,
    p10: 0.26,
    p90: 0.46,
    atm_percentile: 0.04,
    call_10_percentile: 0.05,
    put_10_percentile: 0.07,
    wing_score: 0.06,
    regime: "CHEAP_WINGS",
    series: [],
    ...overrides,
  };
}

describe("thetaStructLabel", () => {
  it("formats short strangle strikes", () => {
    expect(thetaStructLabel(thetaResult(250, 320))).toBe("SHORT 250P / 320C");
  });

  it("keeps fractional strikes without padding", () => {
    expect(thetaStructLabel(thetaResult(66.5, 95))).toBe("SHORT 66.5P / 95C");
  });
});

describe("conePosition", () => {
  it("places ATM IV inside the 90/10 cone", () => {
    expect(conePosition(coneName({ atm_iv: 0.36, p10: 0.26, p90: 0.46 }))).toBeCloseTo(0.5, 6);
  });

  it("clamps a print below the floor and above the ceiling", () => {
    expect(conePosition(coneName({ atm_iv: 0.2 }))).toBe(0);
    expect(conePosition(coneName({ atm_iv: 0.9 }))).toBe(1);
  });

  it("returns null for a missing IV or a degenerate cone", () => {
    expect(conePosition(coneName({ atm_iv: null }))).toBeNull();
    expect(conePosition(coneName({ p10: null }))).toBeNull();
    expect(conePosition(coneName({ p10: 0.4, p90: 0.4 }))).toBeNull();
  });
});

describe("coneFillPct", () => {
  // The bar reads like every other one on the panel: longer is better, and
  // cheap is better, so the floor of the cone fills the whole bar.
  it("fills the bar as the print gets cheaper", () => {
    expect(coneFillPct(coneName({ atm_iv: 0.26 }))).toBe(100);
    expect(coneFillPct(coneName({ atm_iv: 0.36 }))).toBeCloseTo(50, 6);
    expect(coneFillPct(coneName({ atm_iv: 0.46 }))).toBe(0);
  });

  it("reports an unusable cone as unavailable, not as a zero fill", () => {
    // Was `.toBe(0)`. That collided with the legitimate 0 for a name at or
    // above the p90 ceiling — and the bar reads longer-is-better, so a
    // candidate whose bounds failed to compute rendered a real IV number
    // beside an empty bar that reads as maximally rich. R-272.
    expect(coneFillPct(coneName({ atm_iv: null }))).toBeNull();
  });
});

describe("volConeTone", () => {
  it("maps regimes to score tones", () => {
    expect(volConeTone("CHEAP_WINGS")).toBe("strong");
    expect(volConeTone("CHEAP_ATM")).toBe("warn");
    expect(volConeTone("RICH")).toBe("fault");
    expect(volConeTone("NEUTRAL")).toBe("fault");
  });
});

describe("volConeExpiryLabel", () => {
  it("pairs the monthly expiry with its DTE", () => {
    expect(volConeExpiryLabel({ expiry: "2026-09-18", dte: 24 })).toBe("SEP 18 · 24D");
  });
});

describe("formatScanSample", () => {
  // A bare HH:MM let a day-old scan read as the current sample: the panel
  // showed "SAMPLE 11:11" while the newest snapshot was from the previous
  // session. Anything not from today must carry its date.
  const now = new Date("2026-08-12T17:40:00Z");
  const hhmm = (d: Date) =>
    d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

  it("shows time only for a sample taken today", () => {
    const today = new Date("2026-08-12T15:11:00Z");
    expect(formatScanSample(today.toISOString(), now)).toBe(hhmm(today));
  });

  it("qualifies a sample from an earlier day with its date", () => {
    const earlier = new Date("2026-08-11T18:11:00Z");
    const formatted = formatScanSample(earlier.toISOString(), now);
    expect(formatted).toContain(
      earlier.toLocaleDateString([], { month: "short", day: "numeric" }),
    );
    expect(formatted).toContain(hhmm(earlier));
  });

  it("renders a placeholder for missing or unparseable timestamps", () => {
    expect(formatScanSample(null, now)).toBe("—");
    expect(formatScanSample("", now)).toBe("—");
    expect(formatScanSample("not-a-date", now)).toBe("—");
  });
});
