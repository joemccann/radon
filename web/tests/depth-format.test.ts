import { describe, expect, it } from "vitest";
import { depthPriceCh, fmtDepthPrice, fmtSpread } from "@/components/ticker-detail/depthFormat";

describe("fmtSpread", () => {
  it("formats a normal ask-minus-bid spread", () => {
    expect(fmtSpread(412.07, 412.24)).toBe("0.17");
  });

  it("does not print a negative number for a crossed book", () => {
    expect(fmtSpread(413.51, 412.24)).toBe("CROSSED");
  });

  it("returns --- when a side is missing", () => {
    expect(fmtSpread(412.07, null)).toBe("---");
    expect(fmtSpread(null, 412.24)).toBe("---");
  });
});

describe("depthPriceCh — reserved width for a live head value (header jitter, 2026-09-29)", () => {
  it("reserves the same width for 2- and 4-decimal ticks of the same price", () => {
    expect(depthPriceCh(148.1)).toBe(depthPriceCh(148.115));
    expect(depthPriceCh(148.115)).toBe(fmtDepthPrice(148.115).length);
  });

  it("never reserves less than the formatted value needs", () => {
    for (const p of [0.3, -0.3, 4.35, 9.99, 10, 148.1, 5812.25, 24500.2575]) {
      expect(depthPriceCh(p)).toBeGreaterThanOrEqual(fmtDepthPrice(p).length);
    }
  });
});
