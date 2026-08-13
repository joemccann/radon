import { describe, expect, it } from "vitest";
import { toLongRangeSkewPoints } from "@/lib/internalsSkewSeries";

describe("internals skew series contract", () => {
  it("nq_spx_field_has_one_formula_and_unit_across_sources", () => {
    const points = toLongRangeSkewPoints({
      nq: { ticker: "NDX", expiry: null, delta: 25, timeframe: "5Y", data: [
        { date: "2026-08-10", value: -2.1 },
        { date: "2026-08-11", value: -1.2 },
      ] },
      spx: { ticker: "SPX", expiry: null, delta: 25, timeframe: "5Y", data: [
        { date: "2026-08-10", value: -3.5 },
      ] },
    });

    expect(points).toEqual([expect.objectContaining({
      date: "2026-08-10",
      metric: "option_rr_spread",
      nq_skew: 1.4,
      nq_option_rr: -2.1,
      spx_option_rr: -3.5,
    })]);
  });
});
