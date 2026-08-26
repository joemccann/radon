import { describe, expect, it } from "vitest";

import {
  ctaPercentileFromZ,
  reconcileCtaTables,
} from "../lib/ctaPercentiles";

// Verbatim rows from the Turso `menthorq_cta` payload for 2026-08-25. The main
// table carries 0/0/0 for SPX and NQ while the index table carries the same
// two rows — identical positions, identical z — with 0.43/0.81/0.88 and
// 0.38/0.52/0.68. The vision extractor rounded the fractions it was told to
// report as integers, and the page published "0th pctile · MAX SHORT" off a
// +3.66 long carrying a +1.48 z-score.
const AUG_25 = {
  main: [
    { underlying: "E-Mini S&P 500 Index", position_today: 3.66, position_yesterday: 3.85, position_1m_ago: 1.69, percentile_1m: 0, percentile_3m: 0, percentile_1y: 0, z_score_3m: 1.48 },
    { underlying: "CME Nasdaq 100 Index", position_today: 2.52, position_yesterday: 2.69, position_1m_ago: 1.59, percentile_1m: 0, percentile_3m: 0, percentile_1y: 0, z_score_3m: 0.26 },
    { underlying: "10-Year T-Note", position_today: -1.59, position_yesterday: -1.38, position_1m_ago: -2.93, percentile_1m: 71, percentile_3m: 57, percentile_1y: 19, z_score_3m: 0.41 },
    { underlying: "Brent", position_today: -1.33, position_yesterday: -0.83, position_1m_ago: 0.99, percentile_1m: 5, percentile_3m: 2, percentile_1y: 5, z_score_3m: -1.89 },
  ],
  index: [
    { underlying: "E-Mini S&P 500 Index", position_today: 3.66, position_yesterday: 3.85, position_1m_ago: 1.69, percentile_1m: 0.43, percentile_3m: 0.81, percentile_1y: 0.88, z_score_3m: 1.48 },
    { underlying: "CME Nasdaq 100 Index", position_today: 2.52, position_yesterday: 2.69, position_1m_ago: 1.59, percentile_1m: 0.38, percentile_3m: 0.52, percentile_1y: 0.68, z_score_3m: 0.26 },
  ],
  commodity: [
    { underlying: "Brent", position_today: -1.33, position_yesterday: -0.83, position_1m_ago: 0.99, percentile_1m: 5, percentile_3m: 2, percentile_1y: 5, z_score_3m: -1.89 },
  ],
  currency: [],
};

describe("ctaPercentileFromZ", () => {
  it("maps a 3M z-score onto the percentile it implies", () => {
    expect(Math.round(ctaPercentileFromZ(0))).toBe(50);
    expect(Math.round(ctaPercentileFromZ(1.48))).toBe(93);
    expect(Math.round(ctaPercentileFromZ(-1.89))).toBe(3);
  });

  it("returns null when the z-score is missing or not finite", () => {
    expect(ctaPercentileFromZ(null)).toBeNull();
    expect(ctaPercentileFromZ(Number.NaN)).toBeNull();
  });
});

describe("reconcileCtaTables", () => {
  it("repairs a rounded percentile from the same row in another table", () => {
    const out = reconcileCtaTables(AUG_25);
    const spx = out.main[0];
    expect(spx.percentile_3m).toBe(81);
    expect(spx.percentile_1m).toBe(43);
    expect(spx.percentile_1y).toBe(88);
    const nq = out.main[1];
    expect(nq.percentile_3m).toBe(52);
  });

  it("normalizes every percentile onto a 0-100 scale", () => {
    const out = reconcileCtaTables(AUG_25);
    expect(out.index[0].percentile_3m).toBe(81);
    expect(out.index[1].percentile_1y).toBe(68);
  });

  it("leaves rows whose percentiles already agree with their z-score alone", () => {
    const out = reconcileCtaTables(AUG_25);
    expect(out.main[2].percentile_3m).toBe(57);
    expect(out.main[3].percentile_3m).toBe(2);
    expect(out.commodity[0].percentile_3m).toBe(2);
  });

  it("nulls a percentile its z-score flatly contradicts when no duplicate can repair it", () => {
    // 2026-08-18 currency: the whole table came back rounded to 0, and no
    // other table carries those contracts. A "0th pctile" on a +1.52 z is not
    // a number to publish.
    const out = reconcileCtaTables({
      main: [],
      index: [],
      commodity: [],
      currency: [
        { underlying: "British Pound", position_today: 1.3, position_yesterday: 1.2, position_1m_ago: -0.4, percentile_1m: 0, percentile_3m: 0, percentile_1y: 0, z_score_3m: 1.52 },
        { underlying: "Brazilian Real", position_today: -0.1, position_yesterday: 0.1, position_1m_ago: 0.3, percentile_1m: 10, percentile_3m: 3, percentile_1y: 2, z_score_3m: -1.37 },
      ],
    });
    expect(out.currency[0].percentile_3m).toBeNull();
    expect(out.currency[0].percentile_1m).toBeNull();
    expect(out.currency[0].percentile_1y).toBeNull();
    // The row that is internally consistent survives untouched.
    expect(out.currency[1].percentile_3m).toBe(3);
  });

  it("keeps a genuine 0th percentile that its z-score corroborates", () => {
    const out = reconcileCtaTables({
      main: [
        { underlying: "Dollar Index", position_today: -2.1, position_yesterday: -2.0, position_1m_ago: 1.1, percentile_1m: 1, percentile_3m: 0, percentile_1y: 2, z_score_3m: -2.4 },
      ],
      index: [],
      commodity: [],
      currency: [],
    });
    expect(out.main[0].percentile_3m).toBe(0);
  });

  it("passes null tables through untouched", () => {
    expect(reconcileCtaTables(null)).toBeNull();
  });
});

describe("reconcileCtaTables scale detection", () => {
  it("reads a 1.0 beside fractional siblings as the 100th percentile, not the 1st", () => {
    // 2026-08-25 commodity: Coffee came back as 1.0 / 0.98 / 0.89. Read cell by
    // cell, the 1.0 renders "1st" — a max-crowded long reported as max short.
    const out = reconcileCtaTables({
      commodity: [
        { underlying: "Coffee", position_today: 1.4, position_yesterday: 1.06, position_1m_ago: 0.53, percentile_1m: 1.0, percentile_3m: 0.98, percentile_1y: 0.89, z_score_3m: 1.07 },
      ],
    });
    expect(out.commodity[0].percentile_1m).toBe(100);
    expect(out.commodity[0].percentile_3m).toBe(98);
    expect(out.commodity[0].percentile_1y).toBe(89);
  });

  it("leaves an integer row on the 0-100 scale it already uses", () => {
    const out = reconcileCtaTables({
      commodity: [
        { underlying: "Corn", position_today: 0.84, position_yesterday: 0.62, position_1m_ago: 0.95, percentile_1m: 95, percentile_3m: 95, percentile_1y: 67, z_score_3m: 1.63 },
      ],
    });
    expect(out.commodity[0].percentile_3m).toBe(95);
  });
});
