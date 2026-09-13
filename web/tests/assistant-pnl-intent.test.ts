import { describe, expect, it } from "vitest";

import {
  detectRealizedPnlWindow,
  formatRealizedPnlAnswer,
} from "@/lib/assistant/pnlIntent";

const TODAY = "2026-09-11";

describe("detectRealizedPnlWindow", () => {
  it("maps 'september 2026' P&L / trades prompts to the full calendar month", () => {
    expect(
      detectRealizedPnlWindow(
        "analyze all my trades for september 2026 and tell me my P&L",
        TODAY,
      ),
    ).toEqual({ from: "2026-09-01", to: "2026-09-30" });
    expect(detectRealizedPnlWindow("Sep 2026 realized pnl", TODAY)).toEqual({
      from: "2026-09-01",
      to: "2026-09-30",
    });
    expect(detectRealizedPnlWindow("performance in Sept. 2026", TODAY)).toEqual({
      from: "2026-09-01",
      to: "2026-09-30",
    });
  });

  it("resolves this month, ytd, this week, and weekly relative to the ET today pin", () => {
    expect(detectRealizedPnlWindow("mtd P&L", TODAY)).toEqual({
      from: "2026-09-01",
      to: TODAY,
    });
    expect(detectRealizedPnlWindow("year to date realized pnl", TODAY)).toEqual({
      from: "2026-01-01",
      to: TODAY,
    });
    expect(detectRealizedPnlWindow("this week's trades", TODAY)).toEqual({
      from: "2026-09-07",
      to: TODAY,
    });
    expect(detectRealizedPnlWindow("Weekly P&L?", TODAY)).toEqual({
      from: "2026-09-05",
      to: TODAY,
    });
  });

  it("accepts an explicit ISO from/to range on a P&L question", () => {
    expect(
      detectRealizedPnlWindow("realized pnl from 2026-09-01 to 2026-09-10", TODAY),
    ).toEqual({ from: "2026-09-01", to: "2026-09-10" });
  });

  it("returns null when the prompt is not a period P&L / trade-history question", () => {
    expect(detectRealizedPnlWindow("What is SPY flow?", TODAY)).toBeNull();
    expect(detectRealizedPnlWindow("buy 1 AAPL", TODAY)).toBeNull();
    expect(detectRealizedPnlWindow("september 2026 weather", TODAY)).toBeNull();
  });
});

describe("formatRealizedPnlAnswer", () => {
  it("renders a total and per-trip lines from a get_realized_pnl payload", () => {
    const text = formatRealizedPnlAnswer({
      from: "2026-09-01",
      to: "2026-09-30",
      total_realized_pnl: 1234.5,
      count: 2,
      round_trips: [
        { ticker: "SNDK", closed: "2026-09-04", realized_pnl: 2000 },
        { ticker: "ARM", closed: "2026-09-08", realized_pnl: -765.5 },
      ],
      note: "Net of commissions.",
    });
    expect(text).toContain("2026-09-01 to 2026-09-30");
    expect(text).toContain("+1234.50");
    expect(text).toContain("2 round trips");
    expect(text).toContain("SNDK 2026-09-04 +2000.00");
    expect(text).toContain("ARM 2026-09-08 -765.50");
    expect(text).toContain("Net of commissions.");
    expect(text).not.toContain("—");
  });

  it("returns null when the payload is not a realized P&L summary", () => {
    expect(formatRealizedPnlAnswer({ hits: 1 })).toBeNull();
    expect(formatRealizedPnlAnswer(null)).toBeNull();
  });

  it("unwraps the executeTool fencePayload envelope used in production", () => {
    const text = formatRealizedPnlAnswer({
      truncated: false,
      status: 200,
      body: {
        from: "2026-09-01",
        to: "2026-09-30",
        total_realized_pnl: 50,
        count: 1,
        round_trips: [{ ticker: "SNDK", closed: "2026-09-04", realized_pnl: 50 }],
      },
    });
    expect(text).toContain("+50.00");
    expect(text).toContain("SNDK");
  });
});
