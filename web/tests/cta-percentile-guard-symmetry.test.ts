// R-288 / R-289 / R-290 (REL-099): the CTA percentile guard becomes two-sided
// and stops trusting rows nothing can verify.
//
// MenthorQ renders percentiles as 0-1 fractions on some cards; the vision
// extractor is told to report integers, so on a fractional card it rounds the
// whole trio to 0 or 1. `z_score_3m` is extracted independently and unrounded,
// which makes it the check.
//
//  (a) The flat 35-point gate is one-sided. Rounding DOWN (0.30 -> 0) produces
//      a gap of 30 and sails through; rounding UP (0.65 -> 1) produces a gap of
//      64 and is caught. The low half is exactly the half that inverts a
//      narrative into "max short".
//  (b) A row with no usable z-score is UNVERIFIABLE, and was published anyway.
//  (c) A trio of integral floats from a fractional card reads 1.0 as the 1st
//      percentile instead of the 100th.

import { describe, expect, it } from "vitest";

import { reconcileCtaTables } from "@/lib/ctaPercentiles";

/** Inverse of the normal CDF used by the guard, so fixtures stay self-consistent. */
function zForPercentile(p: number): number {
  let lo = -6;
  let hi = 6;
  for (let i = 0; i < 200; i += 1) {
    const mid = (lo + hi) / 2;
    const cdf = 0.5 * (1 + erf(mid / Math.SQRT2)) * 100;
    if (cdf < p) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

function erf(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const z = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * z);
  const y =
    1 -
    ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t +
      0.254829592) *
      t *
      Math.exp(-z * z);
  return sign * y;
}

function row(over: Record<string, unknown> = {}) {
  return {
    underlying: "E-Mini S&P 500 Index",
    position_today: 1.2,
    position_yesterday: 1.1,
    position_1m_ago: 0.4,
    percentile_1m: 0,
    percentile_3m: 0,
    percentile_1y: 0,
    z_score_3m: 0,
    ...over,
  };
}

const p3 = (t: Record<string, { percentile_3m?: number | null }[]>) =>
  t.equities[0].percentile_3m;

describe("(a) the guard catches rounding in BOTH directions", () => {
  it("nulls a 0.30 percentile that the extractor rounded to 0", () => {
    // z = -0.52 implies ~30. Stored 0. Gap 30 — under the old flat 35 limit.
    const out = reconcileCtaTables({
      equities: [row({ percentile_3m: 0, z_score_3m: -0.52 })],
    });
    expect(p3(out as never)).toBeNull();
  });

  it("nulls every rounded-down fraction across 0.03-0.45", () => {
    const survivors: number[] = [];
    for (let pct = 3; pct <= 45; pct += 1) {
      const out = reconcileCtaTables({
        equities: [row({ percentile_3m: 0, z_score_3m: zForPercentile(pct) })],
      });
      if (p3(out as never) !== null) survivors.push(pct);
    }
    expect(survivors).toEqual([]);
  });

  it("still nulls the rounded-up 0.65 case it already caught", () => {
    const out = reconcileCtaTables({
      equities: [row({ percentile_3m: 1, z_score_3m: zForPercentile(65) })],
    });
    expect(p3(out as never)).toBeNull();
  });

  it("keeps a genuine 0th percentile whose z agrees", () => {
    const out = reconcileCtaTables({
      equities: [row({ percentile_3m: 0, z_score_3m: -3.5 })],
    });
    expect(p3(out as never)).toBe(0);
  });

  it("keeps a real integer percentile inside the extraction-noise band", () => {
    // Stored 57, z implies ~66: a 9-point gap is ordinary extractor noise on a
    // real integer card and must not blank the column.
    const out = reconcileCtaTables({
      equities: [row({ percentile_3m: 57, percentile_1m: 71, percentile_1y: 19, z_score_3m: 0.41 })],
    });
    expect(p3(out as never)).toBe(57);
  });
});

describe("(b) an unverifiable percentile is not published", () => {
  for (const [label, z] of [
    ["absent", undefined],
    ["null", null],
    ["empty string", ""],
    ["NaN", Number.NaN],
  ] as const) {
    it(`nulls percentile_3m when z_score_3m is ${label}`, () => {
      const r = row({ percentile_3m: 42, percentile_1m: 40, percentile_1y: 44 });
      if (z === undefined) delete (r as Record<string, unknown>).z_score_3m;
      else (r as Record<string, unknown>).z_score_3m = z;
      expect(p3(reconcileCtaTables({ equities: [r] }) as never)).toBeNull();
    });
  }
});

describe("(c) an all-integral trio from a fractional card", () => {
  it("reads 1.0 as the 100th percentile, not the 1st", () => {
    const out = reconcileCtaTables({
      equities: [
        row({
          percentile_1m: 0.0,
          percentile_3m: 1.0,
          percentile_1y: 0.0,
          z_score_3m: zForPercentile(100),
        }),
      ],
    });
    expect(p3(out as never)).toBe(100);
  });
});
