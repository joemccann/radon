import { describe, expect, it } from "vitest";
import {
  formatShortDate,
  journalStructureNotation,
  positionHoldingNotation,
} from "@/lib/traderNotation";

describe("traderNotation", () => {
  it("formats dates as month and day without zero padding", () => {
    expect(formatShortDate("2026-10-12")).toBe("Oct 12");
    expect(formatShortDate("2026-09-01T12:00:00Z")).toBe("Sep 1");
    expect(formatShortDate("garbage")).toBeNull();
  });

  it("rewrites journal option structures in trader notation", () => {
    expect(journalStructureNotation("Short Call $270 2026-10-30")).toBe("-270C Oct 30");
    expect(journalStructureNotation("Long Put $85.5 2026-11-06")).toBe("85.5P Nov 6");
    expect(journalStructureNotation("Closed Put $1200 2026-11-06")).toBe("close 1200P Nov 6");
    expect(journalStructureNotation("Short Spread (BAG)")).toBe("Short Spread (BAG)");
  });

  it("renders a single-option holding as strike plus right", () => {
    const leg = {
      direction: "LONG" as const,
      contracts: 1,
      type: "Call" as const,
      strike: 300,
      entry_cost: 0,
      avg_cost: 0,
      market_price: null,
      market_value: null,
    };
    expect(positionHoldingNotation({ structure: "Long Call $300.0", legs: [leg] })).toBe("300C");
    expect(
      positionHoldingNotation({ structure: "Short Put", legs: [{ ...leg, direction: "SHORT", type: "Put", strike: 20 }] }),
    ).toBe("-20P");
    expect(positionHoldingNotation({ structure: "Bull Call Spread", legs: [leg, leg] })).toBe("Bull Call Spread");
  });
});
