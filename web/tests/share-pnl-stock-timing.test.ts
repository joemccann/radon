import { describe, expect, it } from "vitest";
import { groupExecutedOrders, positionGroupShareData } from "../components/WorkspaceSections";
import { formatHoldDuration } from "../lib/holdTime";
import { resolveStockEntryTime } from "../lib/orders/stockEntryTime";
import type { ExecutedOrder } from "../lib/types";
import tqqqFills from "./fixtures/pnl-story-tqqq-fills.json";

// Canonical executed_orders read for the supplied 2026-10-13 story.
// Account/order metadata removed and execution IDs replaced; economic fields
// and timestamps preserved. These twenty executions fully close 10,000 shares.
const observed = tqqqFills as ExecutedOrder[];

function fill(id: string, side: string, quantity: number, time: string, closing = false): ExecutedOrder {
  return {
    execId: id, symbol: "TQQQ", contract: { symbol: "TQQQ", secType: "STK", conId: 72539702 },
    side, quantity, time, avgPrice: 78, commission: 0, realizedPNL: closing ? 1 : 0, exchange: "SMART",
  };
}

function shareFor(fills: ExecutedOrder[], closingId: string) {
  const groups = groupExecutedOrders(fills);
  const close = groups.find((g) => g.fills.some((f) => f.execId === closingId))!;
  return positionGroupShareData(close, groups, [], { TQQQ: "2026-10-13" });
}

describe("stock P&L share entry timing", () => {
  it("recovers the pictured fully closed TQQQ entry timestamp and 44-minute hold", () => {
    const groups = groupExecutedOrders(observed);
    const close = groups.find((g) => g.isClosing)!;
    expect(close.totalQuantity).toBe(10_000);
    expect(close.totalPnL).toBeCloseTo(11_435.65, 2);
    const data = positionGroupShareData(close, groups, [], { TQQQ: "2026-10-13" });
    expect(data.entryTime).toBe("2026-10-13T15:46:31+00:00");
    expect(data.exitTime).toBe("2026-10-13T16:30:30+00:00");
    expect(formatHoldDuration(data.entryTime, data.exitTime)).toBe("44 minutes");
    // Timing repair must preserve the existing realized-P&L-implied basis.
    expect(data.entryPrice).toBeCloseTo(77.70643546, 8);
  });

  it("covers a short stock position from its selling entry", () => {
    const data = shareFor([
      fill("short", "SLD", 50, "2026-10-13T14:00:00Z"),
      fill("cover", "BOT", 50, "2026-10-13T15:00:00Z", true),
    ], "cover");
    expect(data.entryTime).toBe("2026-10-13T14:00:00Z");
  });

  it("consumes prior closes before matching a reopened position", () => {
    const data = shareFor([
      fill("first", "BOT", 50, "2026-10-13T13:00:00Z"),
      fill("closed-first", "SLD", 50, "2026-10-13T14:00:00Z", true),
      fill("second", "BOT", 50, "2026-10-13T15:00:00Z"),
      fill("closed-second", "SLD", 50, "2026-10-13T16:00:00Z", true),
    ], "closed-second");
    expect(data.entryTime).toBe("2026-10-13T15:00:00Z");
  });

  it("retains the oldest unconsumed entry across partial closes", () => {
    const data = shareFor([
      fill("first", "BOT", 100, "2026-10-13T13:00:00Z"),
      fill("partial", "SLD", 40, "2026-10-13T14:00:00Z", true),
      fill("second", "BOT", 20, "2026-10-13T15:00:00Z"),
      fill("remaining", "SLD", 80, "2026-10-13T16:00:00Z", true),
    ], "remaining");
    expect(data.entryTime).toBe("2026-10-13T13:00:00Z");
  });

  it("does not invent entry timing when visible opening quantity is insufficient", () => {
    const data = shareFor([
      fill("open", "BOT", 20, "2026-10-13T15:00:00Z"),
      fill("close", "SLD", 50, "2026-10-13T16:00:00Z", true),
    ], "close");
    expect(data.entryTime).toBeNull();
  });

  it("does not substitute a later opening, different contract or wrong-side entry", () => {
    for (const bad of [
      fill("later", "BOT", 50, "2026-10-13T17:00:00Z"),
      { ...fill("other", "BOT", 50, "2026-10-13T15:00:00Z"), contract: { symbol: "TQQQ", secType: "STK", conId: 99 } },
      fill("wrong-side", "SLD", 50, "2026-10-13T15:00:00Z"),
    ]) {
      expect(shareFor([bad, fill("close", "SLD", 50, "2026-10-13T16:00:00Z", true)], "close").entryTime).toBeNull();
    }
  });

  it("retains an execution timestamp when a matching live stock position has only an entry day", () => {
    const groups = groupExecutedOrders(observed);
    const close = { ...groups.find((g) => g.isClosing)!, description: "Closed TQQQ Long Stock" };
    const data = positionGroupShareData(close, groups, [{
      ticker: "TQQQ", structure: "Long Stock", entry_date: "2026-10-13", contracts: 100,
      legs: [{ type: "Stock", avg_cost: 77.7 }],
    } as import("../lib/types").PortfolioPosition]);
    expect(data.entryTime).toBe("2026-10-13T15:46:31+00:00");
  });

  it("fails closed for missing groups, empty/non-stock targets and duplicate target executions", () => {
    const close = { isClosing: true, fills: [fill("close", "SLD", 50, "2026-10-13T16:00:00Z", true)] };
    expect(resolveStockEntryTime(close, undefined)).toBeNull();
    expect(resolveStockEntryTime({ ...close, isClosing: false }, [])).toBeNull();
    expect(resolveStockEntryTime({ ...close, fills: [] }, [])).toBeNull();
    expect(resolveStockEntryTime({ ...close, fills: [{ ...close.fills[0], contract: { symbol: "TQQQ", secType: "OPT" } }] }, [])).toBeNull();
    expect(resolveStockEntryTime({ ...close, fills: [...close.fills, ...close.fills] }, [])).toBeNull();
    expect(resolveStockEntryTime(close, [])).toBeNull();
  });

  it("fails closed for unorderable timestamps and invalid quantities or sides", () => {
    const close = { isClosing: true, fills: [fill("close", "SLD", 50, "2026-10-13T16:00:00Z", true)] };
    const open = fill("open", "BOT", 50, "2026-10-13T15:00:00Z");
    expect(resolveStockEntryTime({ ...close, fills: [{ ...close.fills[0], time: "invalid" }] }, [close])).toBeNull();
    for (const bad of [{ ...open, time: "invalid" }, { ...open, quantity: NaN }, { ...open, quantity: 0 }, { ...open, side: "unknown" }]) {
      expect(resolveStockEntryTime(close, [{ isClosing: false, fills: [bad] }, close])).toBeNull();
    }
    expect(resolveStockEntryTime(close, [
      { isClosing: false, fills: [open, fill("suspect", "SLD", 10, "2026-10-13T15:30:00Z")] }, close,
    ])).toBeNull();
  });

  it("deduplicates visible executions and refuses unknown stock identities", () => {
    const open = fill("open", "BUY", 50, "2026-10-13T15:00:00Z");
    const close = { isClosing: true, fills: [fill("close", "SELL", 50, "2026-10-13T16:00:00Z", true)] };
    expect(resolveStockEntryTime(close, [{ isClosing: false, fills: [open, open] }, close])).toBe(open.time);
    for (const conId of [undefined, 0, NaN]) {
      expect(resolveStockEntryTime({ ...close, fills: [{ ...close.fills[0], contract: { symbol: "TQQQ", secType: "STK", conId } }] }, [close])).toBeNull();
    }
  });
});
