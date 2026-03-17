/**
 * TDD: Realized P&L should be derived from today's fills (executed_orders),
 * not from IB's account_summary.realized_pnl.
 *
 * Bug: account_summary.realized_pnl from IB's reqPnL() is -$6,835 even though
 * there are zero fills today. The correct value is $0 when no fills exist.
 */

import { describe, test, expect } from "vitest";
import type { ExecutedOrder } from "../lib/types";

// Import the utility we are about to create
import { computeRealizedPnlFromFills } from "../lib/realized-pnl";

const todaysDate = new Date().toLocaleDateString("sv", {
  timeZone: "America/New_York",
});

const makeFill = (
  realizedPNL: number | null,
  time = `${todaysDate}T10:00:00`,
): ExecutedOrder => ({
  execId: "test-exec-1",
  symbol: "AAPL",
  contract: { symbol: "AAPL", secType: "STK", conId: null, strike: null, right: null, expiry: null },
  side: "BOT",
  quantity: 100,
  avgPrice: 214.5,
  commission: -1.05,
  realizedPNL,
  time,
  exchange: "SMART",
});

describe("computeRealizedPnlFromFills", () => {
  test("returns 0 when there are no fills", () => {
    expect(computeRealizedPnlFromFills([])).toBe(0);
  });

  test("returns 0 when all fills have null realizedPNL (legs that don't close a position)", () => {
    const fills = [makeFill(null), makeFill(null)];
    expect(computeRealizedPnlFromFills(fills)).toBe(0);
  });

  test("sums realized P&L across all fills", () => {
    const fills = [makeFill(100), makeFill(250), makeFill(-50)];
    expect(computeRealizedPnlFromFills(fills)).toBeCloseTo(300);
  });

  test("skips null entries when summing", () => {
    const fills = [makeFill(500), makeFill(null), makeFill(-200)];
    expect(computeRealizedPnlFromFills(fills)).toBeCloseTo(300);
  });

  test("handles negative realized P&L (losing trades)", () => {
    const fills = [makeFill(-1250.50), makeFill(-500.25)];
    expect(computeRealizedPnlFromFills(fills)).toBeCloseTo(-1750.75);
  });

  test("handles single fill with exact P&L", () => {
    const fills = [makeFill(3847.20)];
    expect(computeRealizedPnlFromFills(fills)).toBeCloseTo(3847.20);
  });
});
