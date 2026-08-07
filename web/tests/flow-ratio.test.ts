import { describe, expect, it } from "vitest";
import { resolvePutCallRatio } from "@/lib/flowRatio";

describe("resolvePutCallRatio", () => {
  it("computes put premium divided by call premium", () => {
    expect(resolvePutCallRatio({ call_premium: 35_990_000, put_premium: 5_640_000 }))
      .toBeCloseTo(0.1567, 4);
  });

  it("uses the canonical ratio when premium totals are unavailable", () => {
    expect(resolvePutCallRatio({ put_call_ratio: 0.54 })).toBe(0.54);
  });

  it("inverts the legacy call/put ratio for cached reports", () => {
    expect(resolvePutCallRatio({ call_put_ratio: 6.38 })).toBeCloseTo(0.1567, 4);
  });

  it("returns zero for all-call premium and null for a zero call denominator", () => {
    expect(resolvePutCallRatio({ call_premium: 100, put_premium: 0 })).toBe(0);
    expect(resolvePutCallRatio({ call_premium: 0, put_premium: 100 })).toBeNull();
  });
});
