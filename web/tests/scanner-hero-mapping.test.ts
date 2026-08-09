import { describe, expect, it } from "vitest";
import {
  GATE_CODES,
  gateCells,
  scoreTone,
  strengthBadge,
  thetaStructLabel,
} from "../lib/scannerHero";
import type { StrengthConfirmationResult, ThetaHarvesterResult } from "../lib/types";

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

function strengthResult(
  passedGroups: string[],
  verdict = "WATCHLIST",
): StrengthConfirmationResult {
  const groups = [
    "Q-SCORES",
    "NET GEX",
    "CALL POSITIONING",
    "TERM STRUCTURE",
    "VOLATILITY SMILE",
    "SYSTEMATIC POSITIONING",
    "MARKET BREADTH",
  ];
  return {
    ticker: "ROST",
    verdict,
    score: Math.round((passedGroups.length / 7) * 100),
    groups_passed: passedGroups.length,
    spot: 243.29,
    factors: groups.map((group) => ({
      group,
      passed: passedGroups.includes(group),
      checks_passed: passedGroups.includes(group) ? 2 : 0,
      checks_total: 2,
      source: "UW",
      checks: [],
      notes: [],
    })),
    errors: [],
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

describe("gateCells", () => {
  it("returns 7 cells in canonical order with stable codes", () => {
    const cells = gateCells(strengthResult(["Q-SCORES", "CALL POSITIONING"]));
    expect(cells).toHaveLength(7);
    expect(cells.map((c) => c.code)).toEqual([...GATE_CODES]);
    expect(cells[0]).toMatchObject({ code: "Q", passed: true });
    expect(cells[1]).toMatchObject({ code: "GX", passed: false });
    expect(cells[2]).toMatchObject({ code: "CL", passed: true });
  });

  it("marks a missing factor group as unknown, not failed", () => {
    const result = strengthResult(["Q-SCORES"]);
    result.factors = result.factors.filter((f) => f.group !== "MARKET BREADTH");
    const cells = gateCells(result);
    expect(cells[6]).toMatchObject({ code: "BR", passed: null });
  });

  it("carries the factor group as the title", () => {
    const cells = gateCells(strengthResult([]));
    expect(cells[4].title).toContain("VOLATILITY SMILE");
  });
});

describe("strengthBadge", () => {
  it("maps verdicts to badge tones", () => {
    expect(strengthBadge("REAL_STRENGTH_CONFIRMED")).toEqual({ label: "CONFIRMED", tone: "strong" });
    expect(strengthBadge("WATCHLIST")).toEqual({ label: "PARTIAL", tone: "warn" });
    expect(strengthBadge("WEAK")).toEqual({ label: "WEAK", tone: "fault" });
  });

  it("passes unknown verdicts through with neutral tone", () => {
    expect(strengthBadge("SOMETHING_ELSE")).toEqual({ label: "SOMETHING_ELSE", tone: "neutral" });
  });
});

describe("scoreTone", () => {
  it("buckets score into strong / warn / fault", () => {
    expect(scoreTone(85)).toBe("strong");
    expect(scoreTone(57)).toBe("warn");
    expect(scoreTone(43)).toBe("fault");
  });
});
