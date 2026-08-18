/**
 * VOL CONE operator analysis + defined-risk trade href helpers.
 *
 * Pure functions in web/lib/volCone.ts. No React.
 * Spec: docs/indicators/vol-cone.md + scanner analysis contract.
 */
import { describe, expect, it } from "vitest";

import {
  buildVolConeAnalysis,
  expectedMove,
  listedIncrement,
  recommendVolConeTrade,
  snapListedStrike,
  volConeOrderHref,
  type VolConeName,
} from "@/lib/volCone";

const EM_DASH = /\u2014/;

function amdName(overrides: Partial<VolConeName> = {}): VolConeName {
  return {
    ticker: "AMD",
    spot: 506,
    expiry: "2026-09-18",
    dte: 32,
    atm_iv: 0.5425,
    call_10_iv: 0.55,
    put_10_iv: 0.61,
    call_10_strike: 556.6,
    put_10_strike: 455.4,
    p10: 0.6008,
    p90: 0.72,
    atm_percentile: 0.05,
    call_10_percentile: 0.1,
    put_10_percentile: 0.1,
    wing_score: 0.1,
    regime: "CHEAP_WINGS",
    series: [],
    ...overrides,
  };
}

function hrefParams(href: string): URLSearchParams {
  return new URL(href, "https://radon.test").searchParams;
}

function assertTradeHref(
  href: string,
  expected: { ticker: string; expiry: string; legs: string },
): void {
  const url = new URL(href, "https://radon.test");
  expect(url.pathname).toBe(`/${expected.ticker}`);
  expect(url.searchParams.get("deck")).toBe("c");
  expect(url.searchParams.get("expiry")).toBe(expected.expiry);
  expect(url.searchParams.get("strikes")).toBe("100");
  expect(url.searchParams.get("src")).toBe("vol-cone");
  expect(url.searchParams.get("legs")).toBe(expected.legs);
  expect(decodeURIComponent(href)).toContain("src=vol-cone");
  expect(decodeURIComponent(href)).toContain(expected.legs);
}

describe("listedIncrement — listed strike step from spot", () => {
  it("uses 0.5 below 25, 1 below 200, else 5", () => {
    expect(listedIncrement(12)).toBe(0.5);
    expect(listedIncrement(24.99)).toBe(0.5);
    expect(listedIncrement(25)).toBe(1);
    expect(listedIncrement(50)).toBe(1);
    expect(listedIncrement(199.99)).toBe(1);
    expect(listedIncrement(200)).toBe(5);
    expect(listedIncrement(506)).toBe(5);
    expect(listedIncrement(223.95)).toBe(5);
  });
});

describe("snapListedStrike — round to listedIncrement(spot)", () => {
  it("snaps AMD 10 percent wings to 5-wide listed strikes", () => {
    expect(snapListedStrike(455.4, 506)).toBe(455);
    expect(snapListedStrike(556.6, 506)).toBe(555);
  });

  it("snaps NVDA 10 percent wings to 5-wide listed strikes", () => {
    expect(snapListedStrike(201.555, 223.95)).toBe(200);
    expect(snapListedStrike(246.345, 223.95)).toBe(245);
  });

  it("snaps ATM spot and half-increment names", () => {
    expect(snapListedStrike(506, 506)).toBe(505);
    expect(snapListedStrike(50.4, 50)).toBe(50);
    expect(snapListedStrike(50.5, 50)).toBe(51);
    expect(snapListedStrike(12.25, 20)).toBe(12.5);
  });
});

describe("expectedMove — ATM IV is decimal, not percent", () => {
  it("returns 1-sigma dollars, fraction, and band", () => {
    const move = expectedMove(100, 0.543, 365);
    expect(move).not.toBeNull();
    expect(move!.fraction).toBeCloseTo(0.543, 8);
    expect(move!.dollars).toBeCloseTo(54.3, 8);
    expect(move!.lo).toBeCloseTo(45.7, 8);
    expect(move!.hi).toBeCloseTo(154.3, 8);
    expect([move!.dollars, move!.fraction, move!.lo, move!.hi].every(Number.isFinite)).toBe(true);
  });

  it("matches the AMD fixture: about 81 dollars / 16 percent", () => {
    const move = expectedMove(506, 0.5425, 32);
    expect(move).not.toBeNull();
    expect(move!.dollars).toBeCloseTo(81.279, 2);
    expect(move!.fraction).toBeCloseTo(0.16063, 4);
    expect(move!.lo).toBeCloseTo(424.72, 1);
    expect(move!.hi).toBeCloseTo(587.28, 1);
    expect(move!.dollars).toBeLessThan(200);
  });

  it("returns null when inputs are invalid", () => {
    expect(expectedMove(Number.NaN, 0.5, 30)).toBeNull();
    expect(expectedMove(100, Number.POSITIVE_INFINITY, 30)).toBeNull();
    expect(expectedMove(100, 0.5, 0)).toBeNull();
    expect(expectedMove(100, 0.5, -1)).toBeNull();
    expect(expectedMove(0, 0.5, 30)).toBeNull();
    expect(expectedMove(100, 0, 30)).toBeNull();
    expect(expectedMove(100, -0.1, 30)).toBeNull();
  });
});

describe("recommendVolConeTrade — defined-risk long vol only", () => {
  it("CHEAP_WINGS is a long 10 percent OTM strangle, put first", () => {
    const rec = recommendVolConeTrade(amdName());
    expect(rec.kind).toBe("strangle");
    expect(rec.legs).toEqual([
      { action: "BUY", quantity: 1, strike: 455, right: "P" },
      { action: "BUY", quantity: 1, strike: 555, right: "C" },
    ]);
    expect(rec.href).toBeTruthy();
    assertTradeHref(rec.href!, {
      ticker: "AMD",
      expiry: "2026-09-18",
      legs: "BUY:1x455P,BUY:1x555C",
    });
  });

  it("CHEAP_ATM is a long ATM straddle, call then put", () => {
    const rec = recommendVolConeTrade(amdName({ regime: "CHEAP_ATM" }));
    expect(rec.kind).toBe("straddle");
    expect(rec.legs).toEqual([
      { action: "BUY", quantity: 1, strike: 505, right: "C" },
      { action: "BUY", quantity: 1, strike: 505, right: "P" },
    ]);
    expect(rec.href).toBeTruthy();
    assertTradeHref(rec.href!, {
      ticker: "AMD",
      expiry: "2026-09-18",
      legs: "BUY:1x505C,BUY:1x505P",
    });
  });

  it("RICH, NEUTRAL, or missing numbers return kind null and href null", () => {
    expect(recommendVolConeTrade(amdName({ regime: "RICH" }))).toEqual(
      expect.objectContaining({ kind: null, href: null }),
    );
    expect(recommendVolConeTrade(amdName({ regime: "NEUTRAL" }))).toEqual(
      expect.objectContaining({ kind: null, href: null }),
    );
    expect(
      recommendVolConeTrade(amdName({ put_10_strike: null, call_10_strike: null })),
    ).toEqual(expect.objectContaining({ kind: null, href: null }));
    expect(
      recommendVolConeTrade(amdName({ regime: "CHEAP_ATM", spot: Number.NaN })),
    ).toEqual(expect.objectContaining({ kind: null, href: null }));
  });
});

describe("volConeOrderHref — chain deep link via URLSearchParams", () => {
  it("builds the AMD CHEAP_WINGS strangle href", () => {
    const href = volConeOrderHref(amdName());
    expect(href).toBeTruthy();
    assertTradeHref(href!, {
      ticker: "AMD",
      expiry: "2026-09-18",
      legs: "BUY:1x455P,BUY:1x555C",
    });
    expect(href).toBe(recommendVolConeTrade(amdName()).href);
  });

  it("builds the CHEAP_ATM straddle href", () => {
    const href = volConeOrderHref(amdName({ regime: "CHEAP_ATM" }));
    expect(href).toBeTruthy();
    expect(hrefParams(href!).get("legs")).toBe("BUY:1x505C,BUY:1x505P");
  });

  it("returns null when there is no recommendation", () => {
    expect(volConeOrderHref(amdName({ regime: "NEUTRAL" }))).toBeNull();
    expect(volConeOrderHref(amdName({ regime: "RICH" }))).toBeNull();
    expect(volConeOrderHref(amdName({ expiry: "" }))).toBeNull();
  });
});

describe("buildVolConeAnalysis — operator view model", () => {
  it("explains AMD CHEAP_WINGS as cheap insurance versus its own cone", () => {
    const analysis = buildVolConeAnalysis(amdName());
    expect(analysis.regime).toBe("CHEAP_WINGS");
    expect(analysis.structureLabel).toBe("LONG 10% OTM STRANGLE");
    expect(analysis.expectedMoveDollars).toBe("$81");
    expect(analysis.expectedMovePct).toBe("16%");
    expect(analysis.expectedMoveRange).toMatch(/425/);
    expect(analysis.expectedMoveRange).toMatch(/587/);
    expect(analysis.coneGap).toBeCloseTo(5.83, 2);
    expect(analysis.coneGapLabel).toMatch(/5\.8/);
    expect(analysis.wingStrikes).toEqual({ put: 455, call: 555 });
    expect(analysis.wingsSigma).toBeCloseTo(0.623, 2);
    expect(analysis.thesis).toMatch(/cheap insurance/i);
    expect(analysis.thesis).toMatch(/cone/i);
    expect(analysis.thesis).toMatch(/10 percent OTM strangle/i);
    expect(analysis.winsIf.length).toBeGreaterThan(10);
    expect(analysis.diesIf.length).toBeGreaterThan(10);
    expect(analysis.notEdge).toMatch(/not/i);
    expect(analysis.notEdge).toMatch(/edge/i);
    expect(analysis.href).toBeTruthy();
    assertTradeHref(analysis.href!, {
      ticker: "AMD",
      expiry: "2026-09-18",
      legs: "BUY:1x455P,BUY:1x555C",
    });
    for (const text of [
      analysis.thesis,
      analysis.winsIf,
      analysis.diesIf,
      analysis.notEdge,
      analysis.expectedMoveDollars,
      analysis.expectedMovePct,
      analysis.expectedMoveRange,
      analysis.coneGapLabel,
      analysis.structureLabel,
    ]) {
      expect(text).not.toMatch(EM_DASH);
    }
  });

  it("CHEAP_ATM analysis still returns copy and a straddle href", () => {
    const analysis = buildVolConeAnalysis(amdName({ regime: "CHEAP_ATM" }));
    expect(analysis.regime).toBe("CHEAP_ATM");
    expect(analysis.structureLabel).toBe("LONG ATM STRADDLE");
    expect(analysis.expectedMoveDollars).toBe("$81");
    expect(analysis.wingStrikes).toEqual({ put: 505, call: 505 });
    expect(analysis.thesis).toMatch(/straddle/i);
    expect(analysis.href).toBeTruthy();
    expect(hrefParams(analysis.href!).get("legs")).toBe("BUY:1x505C,BUY:1x505P");
  });

  it("RICH and NEUTRAL still return analysis with href null", () => {
    const rich = buildVolConeAnalysis(amdName({ regime: "RICH" }));
    expect(rich.regime).toBe("RICH");
    expect(rich.structureLabel).toBe("NO TRADE");
    expect(rich.href).toBeNull();
    expect(rich.expectedMoveDollars).toBe("$81");
    expect(rich.wingStrikes).toEqual({ put: 455, call: 555 });
    expect(rich.thesis).not.toMatch(EM_DASH);

    const neutral = buildVolConeAnalysis(amdName({ regime: "NEUTRAL" }));
    expect(neutral.regime).toBe("NEUTRAL");
    expect(neutral.structureLabel).toBe("NO TRADE");
    expect(neutral.href).toBeNull();
    expect(neutral.thesis).toMatch(/cone/i);
  });
});
