import { describe, expect, it } from "vitest";
import { fmtSpread } from "@/components/ticker-detail/depthFormat";

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
