/**
 * Unit tests: Naked short guard — blocks naked short exposure.
 *
 * Validates that checkNakedShortRisk blocks selling stock/calls
 * without sufficient long coverage, while allowing spreads, puts,
 * and buy orders.
 */

import { describe, it, expect } from "vitest";
import {
  checkNakedShortRisk,
  auditOpenOrders,
  type OrderPayload,
  type NakedShortPortfolio,
  type NakedShortOpenOrder,
} from "../lib/nakedShortGuard";

/* ---------- helpers ---------- */

function makePortfolio(positions: NakedShortPortfolio["positions"] = []): NakedShortPortfolio {
  return { positions };
}

function makeOrder(overrides: Partial<OrderPayload>): OrderPayload {
  return {
    type: "stock",
    symbol: "AAPL",
    action: "BUY",
    quantity: 100,
    limitPrice: 250,
    ...overrides,
  };
}

/* ---------- positions ---------- */

const longStock = (ticker: string, shares: number) => ({
  ticker,
  structure_type: "Stock",
  contracts: shares,
  direction: "LONG" as const,
  legs: [{ direction: "LONG" as const, type: "Stock" as const, contracts: shares, strike: null }],
});

const coveredCall = (ticker: string, shares: number, callContracts: number) => ({
  ticker,
  structure_type: "Covered Call",
  contracts: callContracts,
  direction: "CREDIT" as const,
  expiry: "N/A",
  legs: [
    { direction: "LONG" as const, type: "Stock" as const, contracts: shares, strike: null },
    { direction: "SHORT" as const, type: "Call" as const, contracts: callContracts, strike: 300 },
  ],
});

const longOption = (
  ticker: string,
  expiry: string,
  type: "Call" | "Put",
  strike: number,
  contracts: number,
) => ({
  ticker,
  structure_type: `Long ${type}`,
  contracts,
  direction: "LONG" as const,
  expiry,
  legs: [
    { direction: "LONG" as const, type, contracts, strike },
  ],
});

/* ---------- tests ---------- */

// SPX-05: stock branch re-enabled as warn-not-block; option/combo still disabled.
describe("checkNakedShortRisk — stock warn branch (SPX-05)", () => {
  it("SPX-05-1. SELL stock, no position → warn (allowed=true, reason present)", () => {
    const order = makeOrder({ action: "SELL", type: "stock", symbol: "AAPL", quantity: 100 });
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(true);
    expect(result.reason).toBeDefined();
    expect(result.reason).toContain("no long shares held");
    expect(result.reason).toContain("AAPL");
  });

  it("SPX-05-2. SELL stock, selling more than held → warn (allowed=true, reason present)", () => {
    const order = makeOrder({ action: "SELL", type: "stock", symbol: "AAPL", quantity: 200 });
    const portfolio = makePortfolio([longStock("AAPL", 100)]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(true);
    expect(result.reason).toBeDefined();
    expect(result.reason).toContain("selling 200 shares but only 100 held");
  });

  it("SPX-05-3. SELL stock, sufficient shares held → ok (allowed=true, no reason)", () => {
    const order = makeOrder({ action: "SELL", type: "stock", symbol: "AAPL", quantity: 50 });
    const portfolio = makePortfolio([longStock("AAPL", 100)]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(true);
    expect(result.reason).toBeUndefined();
  });

  it("SPX-05-4. SELL call (option short) → allowed, no reason (option branch still disabled)", () => {
    const order = makeOrder({
      action: "SELL",
      type: "option",
      symbol: "AAPL",
      right: "C",
      strike: 300,
      expiry: "2026-04-17",
      quantity: 5,
    });
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(true);
    expect(result.reason).toBeUndefined();
  });

  it("SPX-05-5. BUY stock → allowed, no reason", () => {
    const order = makeOrder({ action: "BUY", type: "stock", symbol: "AAPL" });
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(true);
    expect(result.reason).toBeUndefined();
  });
});

// Guard is disabled at the export boundary; suite skipped.
describe.skip("checkNakedShortRisk", () => {
  it("1. BUY stock → allowed", () => {
    const order = makeOrder({ action: "BUY", type: "stock", symbol: "AAPL" });
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(true);
  });

  it("2. BUY call → allowed", () => {
    const order = makeOrder({
      action: "BUY",
      type: "option",
      symbol: "AAPL",
      right: "C",
      strike: 300,
      expiry: "2026-04-17",
      quantity: 10,
    });
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(true);
  });

  it("3. SELL stock, no position → blocked (naked short stock)", () => {
    const order = makeOrder({ action: "SELL", type: "stock", symbol: "AAPL", quantity: 100 });
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(false);
    expect(result.reason).toContain("no long shares held");
    expect(result.reason).toContain("AAPL");
  });

  it("4. SELL stock, has position, qty ≤ held → allowed", () => {
    const order = makeOrder({ action: "SELL", type: "stock", symbol: "AAPL", quantity: 50 });
    const portfolio = makePortfolio([longStock("AAPL", 100)]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(true);
  });

  it("5. SELL stock, has position, qty > held → blocked", () => {
    const order = makeOrder({ action: "SELL", type: "stock", symbol: "AAPL", quantity: 200 });
    const portfolio = makePortfolio([longStock("AAPL", 100)]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(false);
    expect(result.reason).toContain("selling 200 shares but only 100 held");
  });

  it("6. SELL call, no stock position → blocked (naked short call)", () => {
    const order = makeOrder({
      action: "SELL",
      type: "option",
      symbol: "AAPL",
      right: "C",
      strike: 300,
      expiry: "2026-04-17",
      quantity: 5,
    });
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(false);
    expect(result.reason).toContain("Naked short call");
    expect(result.reason).toContain("AAPL");
  });

  it("7. SELL call, has stock, contracts * 100 ≤ shares → allowed (covered call)", () => {
    const order = makeOrder({
      action: "SELL",
      type: "option",
      symbol: "AAPL",
      right: "C",
      strike: 300,
      expiry: "2026-04-17",
      quantity: 1,
    });
    const portfolio = makePortfolio([longStock("AAPL", 200)]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(true);
  });

  it("8. SELL call, has stock, contracts * 100 > shares → blocked (short a tail)", () => {
    const order = makeOrder({
      action: "SELL",
      type: "option",
      symbol: "AAPL",
      right: "C",
      strike: 300,
      expiry: "2026-04-17",
      quantity: 5,
    });
    const portfolio = makePortfolio([longStock("AAPL", 200)]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(false);
    expect(result.reason).toContain("Short a tail");
    expect(result.reason).toContain("5 calls");
    expect(result.reason).toContain("200 shares cover 2 contracts");
  });

  it("9. SELL put → allowed (cash-secured)", () => {
    const order = makeOrder({
      action: "SELL",
      type: "option",
      symbol: "AAPL",
      right: "P",
      strike: 200,
      expiry: "2026-04-17",
      quantity: 10,
    });
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(true);
  });

  it("10. Combo order with BUY+SELL legs → allowed (spread)", () => {
    const order: OrderPayload = {
      type: "combo",
      symbol: "AAPL",
      action: "BUY",
      quantity: 10,
      limitPrice: 2.5,
      legs: [
        { expiry: "2026-04-17", strike: 270, right: "C", action: "BUY", ratio: 1 },
        { expiry: "2026-04-17", strike: 290, right: "C", action: "SELL", ratio: 1 },
      ],
    };
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(true);
  });

  it("14. Short risk reversal (SELL C + BUY P), no stock → blocked", () => {
    // Gap fix: BUY envelope but legs contain an uncovered SELL call
    const order: OrderPayload = {
      type: "combo",
      symbol: "TSLA",
      action: "BUY",
      quantity: 1,
      limitPrice: 0,
      legs: [
        { expiry: "2026-06-20", strike: 300, right: "C", action: "SELL", ratio: 1 },
        { expiry: "2026-06-20", strike: 260, right: "P", action: "BUY", ratio: 1 },
      ],
    };
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(false);
    expect(result.reason).toContain("Naked short call in combo");
    expect(result.reason).toContain("TSLA");
  });

  it("15. Short risk reversal (SELL C + BUY P), stock covers → allowed", () => {
    const order: OrderPayload = {
      type: "combo",
      symbol: "TSLA",
      action: "BUY",
      quantity: 1,
      limitPrice: 0,
      legs: [
        { expiry: "2026-06-20", strike: 300, right: "C", action: "SELL", ratio: 1 },
        { expiry: "2026-06-20", strike: 260, right: "P", action: "BUY", ratio: 1 },
      ],
    };
    const portfolio = makePortfolio([longStock("TSLA", 100)]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(true);
  });

  it("16. Bull call spread (BUY C + SELL C) → allowed (regression: still correct after fix)", () => {
    const order: OrderPayload = {
      type: "combo",
      symbol: "AAPL",
      action: "BUY",
      quantity: 5,
      limitPrice: 2.5,
      legs: [
        { expiry: "2026-04-17", strike: 270, right: "C", action: "BUY", ratio: 1 },
        { expiry: "2026-04-17", strike: 290, right: "C", action: "SELL", ratio: 1 },
      ],
    };
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(true);
  });

  it("17. Jade Lizard (BUY C + SELL higher C + SELL P) → allowed (call spread covered, put cash-secured)", () => {
    const order: OrderPayload = {
      type: "combo",
      symbol: "AAPL",
      action: "BUY",
      quantity: 1,
      limitPrice: 2.0,
      legs: [
        { expiry: "2026-04-17", strike: 300, right: "C", action: "BUY", ratio: 1 },
        { expiry: "2026-04-17", strike: 310, right: "C", action: "SELL", ratio: 1 },
        { expiry: "2026-04-17", strike: 270, right: "P", action: "SELL", ratio: 1 },
      ],
    };
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(true);
  });

  it("18. 1x2 ratio call spread (BUY 1C + SELL 2C), no stock → blocked (1 uncovered call)", () => {
    const order: OrderPayload = {
      type: "combo",
      symbol: "AAPL",
      action: "BUY",
      quantity: 1,
      limitPrice: 0.5,
      legs: [
        { expiry: "2026-04-17", strike: 280, right: "C", action: "BUY", ratio: 1 },
        { expiry: "2026-04-17", strike: 290, right: "C", action: "SELL", ratio: 2 },
      ],
    };
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(false);
    expect(result.reason).toContain("Naked short call in combo");
  });

  it("19. 1x2 ratio call spread (BUY 1C + SELL 2C), stock covers uncovered call → allowed", () => {
    const order: OrderPayload = {
      type: "combo",
      symbol: "AAPL",
      action: "BUY",
      quantity: 1,
      limitPrice: 0.5,
      legs: [
        { expiry: "2026-04-17", strike: 280, right: "C", action: "BUY", ratio: 1 },
        { expiry: "2026-04-17", strike: 290, right: "C", action: "SELL", ratio: 2 },
      ],
    };
    // 1 uncovered short call × 1 qty × 100 = 100 shares needed
    const portfolio = makePortfolio([longStock("AAPL", 100)]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(true);
  });

  it("20. Closing a combo (action=SELL) → allowed regardless of leg structure", () => {
    // Closing a short risk reversal should not be blocked
    const order: OrderPayload = {
      type: "combo",
      symbol: "TSLA",
      action: "SELL",
      quantity: 1,
      limitPrice: 0,
      legs: [
        { expiry: "2026-06-20", strike: 300, right: "C", action: "SELL", ratio: 1 },
        { expiry: "2026-06-20", strike: 260, right: "P", action: "BUY", ratio: 1 },
      ],
    };
    const result = checkNakedShortRisk(order, makePortfolio());
    expect(result.allowed).toBe(true);
  });

  it("10b. SELL call covered by existing covered-call stock leg → allowed", () => {
    const order = makeOrder({
      action: "SELL",
      type: "option",
      symbol: "AAPL",
      right: "C",
      strike: 310,
      expiry: "2026-05-15",
      quantity: 1,
    });
    // 300 shares from covered call, 2 already sold → 1 remaining cover
    const portfolio = makePortfolio([coveredCall("AAPL", 300, 2)]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(true);
  });

  it("10c. SELL call covered by existing long call at different strike (vertical spread) → allowed", () => {
    // User is LONG 125 AAOI $115 calls, wants to SELL 125 AAOI $125 calls → bull call spread
    const order = makeOrder({
      action: "SELL",
      type: "option",
      symbol: "AAOI",
      right: "C",
      strike: 125,
      expiry: "20260402",
      quantity: 125,
    });
    const portfolio = makePortfolio([
      longOption("AAOI", "2026-04-02", "Call", 115, 125),
    ]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(true);
  });

  it("10d. SELL more calls than long calls at same expiry → blocked (uncovered portion)", () => {
    // LONG 50 calls, trying to SELL 125 → 75 uncovered
    const order = makeOrder({
      action: "SELL",
      type: "option",
      symbol: "AAOI",
      right: "C",
      strike: 125,
      expiry: "20260402",
      quantity: 125,
    });
    const portfolio = makePortfolio([
      longOption("AAOI", "2026-04-02", "Call", 115, 50),
    ]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(false);
  });

  it("10e. SELL call at different expiry than long calls → blocked (no spread)", () => {
    // LONG calls at April expiry, SELL calls at June expiry → not a spread
    const order = makeOrder({
      action: "SELL",
      type: "option",
      symbol: "AAOI",
      right: "C",
      strike: 125,
      expiry: "20260620",
      quantity: 125,
    });
    const portfolio = makePortfolio([
      longOption("AAOI", "2026-04-02", "Call", 115, 125),
    ]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(false);
  });

  it("10f. SELL call matching an existing long call contract → allowed (sell to close)", () => {
    const order = makeOrder({
      action: "SELL",
      type: "option",
      symbol: "WULF",
      right: "C",
      strike: 17,
      expiry: "20270115",
      quantity: 77,
    });
    const portfolio = makePortfolio([
      longOption("WULF", "2027-01-15", "Call", 17, 77),
    ]);
    const result = checkNakedShortRisk(order, portfolio);
    expect(result.allowed).toBe(true);
  });
});

describe.skip("auditOpenOrders", () => {
  it("11. open SELL call order with no stock → returns violation", () => {
    const orders: NakedShortOpenOrder[] = [
      {
        orderId: 1,
        permId: 100,
        symbol: "TSLA",
        action: "SELL",
        totalQuantity: 5,
        contract: { secType: "OPT", right: "C", strike: 300, expiry: "2026-06-20", symbol: "TSLA" },
      },
    ];
    const violations = auditOpenOrders(orders, makePortfolio());
    expect(violations).toHaveLength(1);
    expect(violations[0].orderId).toBe(1);
    expect(violations[0].permId).toBe(100);
    expect(violations[0].reason).toContain("TSLA");
  });

  it("12. open SELL call order with sufficient stock → returns empty", () => {
    const orders: NakedShortOpenOrder[] = [
      {
        orderId: 2,
        permId: 200,
        symbol: "AAPL",
        action: "SELL",
        totalQuantity: 1,
        contract: { secType: "OPT", right: "C", strike: 300, expiry: "2026-06-20", symbol: "AAPL" },
      },
    ];
    const portfolio = makePortfolio([longStock("AAPL", 500)]);
    const violations = auditOpenOrders(orders, portfolio);
    expect(violations).toHaveLength(0);
  });

  it("13. audit accounts for existing short calls in portfolio", () => {
    // Portfolio already has 2 short call contracts (covered call) using 200 of 300 shares
    // Open order wants to sell 2 more calls = total 4 contracts = 400 shares needed, only 300 held
    const orders: NakedShortOpenOrder[] = [
      {
        orderId: 3,
        permId: 300,
        symbol: "AAPL",
        action: "SELL",
        totalQuantity: 2,
        contract: { secType: "OPT", right: "C", strike: 310, expiry: "2026-06-20", symbol: "AAPL" },
      },
    ];
    const portfolio = makePortfolio([coveredCall("AAPL", 300, 2)]);
    const violations = auditOpenOrders(orders, portfolio);
    expect(violations).toHaveLength(1);
    expect(violations[0].reason).toContain("AAPL");
  });

  it("14. audit ignores sell-to-close call orders backed by a matching long call position", () => {
    const orders: NakedShortOpenOrder[] = [
      {
        orderId: 4,
        permId: 400,
        symbol: "WULF",
        action: "SELL",
        totalQuantity: 77,
        contract: { secType: "OPT", right: "C", strike: 17, expiry: "2027-01-15", symbol: "WULF" },
      },
    ];
    const portfolio = makePortfolio([
      longOption("WULF", "2027-01-15", "Call", 17, 77),
    ]);
    const violations = auditOpenOrders(orders, portfolio);
    expect(violations).toHaveLength(0);
  });
});
