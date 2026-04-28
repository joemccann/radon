import { describe, expect, it } from "vitest";
import { bsCall, bsPrice, bsPut, normCdf } from "../lib/blackScholes";

/**
 * Reference values produced by scripts/scenario_analysis.py:
 *   from scripts.scenario_analysis import black_scholes_call, black_scholes_put
 * Embedded so the TS implementation cannot drift from the Python source.
 */
const PARITY_CASES: Array<{
  S: number;
  K: number;
  T: number;
  r: number;
  sigma: number;
  call: number;
  put: number;
  label: string;
}> = [
  { S: 100, K: 100, T: 0.5, r: 0, sigma: 0.25, call: 7.04321, put: 7.04321, label: "ATM half-year r=0 sigma=25%" },
  { S: 100, K: 110, T: 0.5, r: 0, sigma: 0.25, call: 3.441223, put: 13.441223, label: "OTM call / ITM put" },
  { S: 100, K: 90, T: 0.5, r: 0, sigma: 0.25, call: 12.841165, put: 2.841165, label: "ITM call / OTM put" },
  { S: 100, K: 100, T: 0.5, r: 0.05, sigma: 0.25, call: 8.260004, put: 5.790995, label: "ATM r=5%" },
  { S: 295, K: 295, T: 3 / 365, r: 0, sigma: 0.45, call: 4.800991, put: 4.800991, label: "AMD ATM 3DTE" },
  { S: 295, K: 300, T: 3 / 365, r: 0, sigma: 0.45, call: 2.746739, put: 7.746739, label: "AMD $5 OTM call 3DTE" },
  { S: 50, K: 50, T: 1.0, r: 0, sigma: 0.3, call: 5.961771, put: 5.961771, label: "ATM 1Y sigma=30%" },
];

describe("normCdf", () => {
  it.each([
    [-2.0, 0.02275],
    [-1.0, 0.158655],
    [-0.5, 0.308538],
    [0.0, 0.5],
    [0.5, 0.691462],
    [1.0, 0.841345],
    [2.0, 0.97725],
  ])("normCdf(%f) ≈ %f", (x, expected) => {
    expect(normCdf(x)).toBeCloseTo(expected, 5);
  });
});

describe("bsCall / bsPut — Python parity", () => {
  for (const c of PARITY_CASES) {
    it(`call(${c.label}) ≈ ${c.call}`, () => {
      expect(bsCall(c.S, c.K, c.T, c.r, c.sigma)).toBeCloseTo(c.call, 4);
    });
    it(`put(${c.label}) ≈ ${c.put}`, () => {
      expect(bsPut(c.S, c.K, c.T, c.r, c.sigma)).toBeCloseTo(c.put, 4);
    });
  }
});

describe("bsPrice dispatch", () => {
  it("routes Call to bsCall", () => {
    const p = bsPrice({ S: 100, K: 100, T: 0.5, r: 0, sigma: 0.25, type: "Call" });
    expect(p).toBeCloseTo(7.04321, 4);
  });
  it("routes Put to bsPut", () => {
    const p = bsPrice({ S: 100, K: 90, T: 0.5, r: 0, sigma: 0.25, type: "Put" });
    expect(p).toBeCloseTo(2.841165, 4);
  });
});

describe("Edge cases", () => {
  it("T=0 call returns intrinsic max(S-K,0)", () => {
    expect(bsCall(50, 50, 0, 0, 0.3)).toBe(0);
    expect(bsCall(60, 50, 0, 0, 0.3)).toBe(10);
    expect(bsCall(40, 50, 0, 0, 0.3)).toBe(0);
  });

  it("T=0 put returns intrinsic max(K-S,0)", () => {
    expect(bsPut(50, 50, 0, 0, 0.3)).toBe(0);
    expect(bsPut(40, 50, 0, 0, 0.3)).toBe(10);
    expect(bsPut(60, 50, 0, 0, 0.3)).toBe(0);
  });

  it("T<0 returns intrinsic (treated as expired)", () => {
    expect(bsCall(60, 50, -0.01, 0, 0.3)).toBe(10);
    expect(bsPut(40, 50, -0.01, 0, 0.3)).toBe(10);
  });

  it("sigma=0 call returns max(S - K·e^(-rT), 0)", () => {
    expect(bsCall(100, 100, 0.5, 0, 0)).toBeCloseTo(0, 6);
    const expected = Math.max(100 - 100 * Math.exp(-0.05 * 0.5), 0);
    expect(bsCall(100, 100, 0.5, 0.05, 0)).toBeCloseTo(expected, 6);
  });

  it("sigma=0 put returns max(K·e^(-rT) - S, 0)", () => {
    expect(bsPut(100, 100, 0.5, 0, 0)).toBeCloseTo(0, 6);
    const expected = Math.max(110 * Math.exp(-0.05 * 0.5) - 100, 0);
    expect(bsPut(100, 110, 0.5, 0.05, 0)).toBeCloseTo(expected, 6);
  });
});

describe("Put-call parity", () => {
  it("at r=0 satisfies C - P = S - K (within numeric tolerance)", () => {
    for (const c of PARITY_CASES.filter((x) => x.r === 0)) {
      const lhs = bsCall(c.S, c.K, c.T, 0, c.sigma) - bsPut(c.S, c.K, c.T, 0, c.sigma);
      expect(lhs).toBeCloseTo(c.S - c.K, 4);
    }
  });

  it("at r>0 satisfies C - P = S - K·e^(-rT)", () => {
    const S = 100,
      K = 100,
      T = 0.5,
      r = 0.05,
      sigma = 0.25;
    const lhs = bsCall(S, K, T, r, sigma) - bsPut(S, K, T, r, sigma);
    expect(lhs).toBeCloseTo(S - K * Math.exp(-r * T), 4);
  });
});
