import { describe, expect, it } from "vitest";
import type { TradeEntry } from "../lib/types";
import {
  filterTradesByRange,
  isClosedTrade,
  journalActivityDate,
  journalCloseDate,
  rangeForPreset,
  summarizeRangePnl,
  toEtDay,
} from "../lib/journal/rangePnl";

function trade(partial: Partial<TradeEntry> & { filled_at?: string }): TradeEntry & { filled_at?: string } {
  return {
    id: partial.id ?? 1,
    date: partial.date ?? "2026-07-01",
    ticker: partial.ticker ?? "MU",
    structure: partial.structure ?? "Long Call",
    decision: partial.decision ?? "EXECUTED",
    ...partial,
  };
}

describe("toEtDay", () => {
  it("keeps date-only strings", () => {
    expect(toEtDay("2026-07-09")).toBe("2026-07-09");
  });

  it("maps ISO with offset to ET calendar day", () => {
    // 2026-07-09 10:31 ET
    expect(toEtDay("2026-07-09T10:31:16-04:00")).toBe("2026-07-09");
    // late UTC can still be prior ET day
    expect(toEtDay("2026-07-09T03:00:00.000Z")).toBe("2026-07-08");
  });
});

describe("isClosedTrade / journalCloseDate", () => {
  it("treats non-zero finite realized_pnl as closed without misclassifying open zero rows", () => {
    expect(isClosedTrade(trade({ realized_pnl: 100 }))).toBe(true);
    expect(isClosedTrade(trade({ realized_pnl: 0 }))).toBe(false);
  });

  it("treats BUY_TO_CLOSE / CLOSED action as closed without pnl", () => {
    expect(isClosedTrade(trade({ action: "BUY_TO_CLOSE" }))).toBe(true);
    expect(isClosedTrade(trade({ decision: "CLOSED" }))).toBe(true);
  });

  it("open long is not closed", () => {
    expect(isClosedTrade(trade({ decision: "OPEN", action: "BUY_OPTION" }))).toBe(false);
  });

  it("prefers close_date for closed trades", () => {
    const t = trade({
      date: "2026-06-01",
      close_date: "2026-07-09",
      filled_at: "2026-07-09T15:00:00-04:00",
      realized_pnl: 50,
    });
    expect(journalCloseDate(t)).toBe("2026-07-09");
  });

  it("activity date falls back filled_at → date", () => {
    expect(journalActivityDate(trade({ filled_at: "2026-07-08T10:00:00-04:00", date: "2026-01-01" }))).toBe(
      "2026-07-08",
    );
    expect(journalActivityDate(trade({ date: "2026-05-01" }))).toBe("2026-05-01");
  });
});

describe("filterTradesByRange + summarizeRangePnl", () => {
  const closeEarly = trade({
    id: 1,
    date: "2026-07-01",
    close_date: "2026-07-01",
    realized_pnl: 1000,
    decision: "CLOSED",
  });
  const closeMid = trade({
    id: 2,
    date: "2026-07-05",
    close_date: "2026-07-05",
    realized_pnl: -200,
    decision: "CLOSED",
  });
  const openLate = trade({
    id: 3,
    date: "2026-07-10",
    decision: "OPEN",
    action: "BUY_OPTION",
  });
  const closePrior = trade({
    id: 4,
    date: "2026-06-15",
    close_date: "2026-06-15",
    realized_pnl: 5000,
    decision: "CLOSED",
  });

  it("closed mode includes only closes in range and sums P&L", () => {
    const inRange = filterTradesByRange(
      [closeEarly, closeMid, openLate, closePrior],
      "2026-07-01",
      "2026-07-07",
      "closed",
    );
    expect(inRange.map((t) => t.id).sort()).toEqual([1, 2]);
    const summary = summarizeRangePnl(inRange);
    expect(summary.realizedPnl).toBe(800);
    expect(summary.closedCount).toBe(2);
    expect(summary.winners).toBe(1);
    expect(summary.losers).toBe(1);
    expect(summary.openCount).toBe(0);
  });

  it("excludes prior-day closes (ET)", () => {
    const inRange = filterTradesByRange([closePrior, closeEarly], "2026-07-01", "2026-07-01", "closed");
    expect(inRange).toHaveLength(1);
    expect(inRange[0].id).toBe(1);
  });

  it("activity mode includes opens in range without adding to realized", () => {
    const inRange = filterTradesByRange(
      [closeMid, openLate],
      "2026-07-01",
      "2026-07-31",
      "activity",
    );
    expect(inRange.map((t) => t.id).sort()).toEqual([2, 3]);
    const summary = summarizeRangePnl(inRange);
    expect(summary.realizedPnl).toBe(-200);
    expect(summary.openCount).toBe(1);
    expect(summary.closedCount).toBe(1);
    expect(summary.tradeCount).toBe(2);
  });

  it("null realized_pnl never enters the sum", () => {
    const closedNoPnl = trade({
      id: 9,
      decision: "CLOSED",
      close_date: "2026-07-02",
      action: "BUY_TO_CLOSE",
    });
    const summary = summarizeRangePnl([closedNoPnl, closeEarly]);
    expect(summary.realizedPnl).toBe(1000);
    expect(summary.closedCount).toBe(2);
  });

  it("unbounded range (all) keeps every closed trade", () => {
    const inRange = filterTradesByRange([closeEarly, closePrior], null, null, "closed");
    expect(inRange).toHaveLength(2);
  });
});

describe("rangeForPreset", () => {
  const now = new Date("2026-07-09T18:00:00.000Z"); // 14:00 ET

  it("mtd is first of month through today ET", () => {
    expect(rangeForPreset("mtd", now)).toEqual({ from: "2026-07-01", to: "2026-07-09" });
  });

  it("today is a single ET day", () => {
    expect(rangeForPreset("today", now)).toEqual({ from: "2026-07-09", to: "2026-07-09" });
  });

  it("7d is inclusive six days back", () => {
    expect(rangeForPreset("7d", now)).toEqual({ from: "2026-07-03", to: "2026-07-09" });
  });

  it("ytd starts Jan 1 ET", () => {
    expect(rangeForPreset("ytd", now)).toEqual({ from: "2026-01-01", to: "2026-07-09" });
  });

  it("all is unbounded", () => {
    expect(rangeForPreset("all", now)).toEqual({ from: null, to: null });
  });
});
