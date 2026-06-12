import { describe, expect, it } from "vitest";
import { closedGroupReturnPct, positionGroupShareData, type PositionFillGroup } from "../components/WorkspaceSections";
import type { ExecutedOrder } from "../lib/types";

function makeOptionFill(
  overrides: Partial<ExecutedOrder> & { contract?: Partial<ExecutedOrder["contract"]> } = {},
): ExecutedOrder {
  const { contract: contractOverrides, ...rest } = overrides;
  return {
    execId: rest.execId ?? "opt-fill",
    symbol: rest.symbol ?? "AAOI",
    contract: {
      conId: 1001,
      symbol: "AAOI",
      secType: "OPT",
      strike: 90,
      right: "C",
      expiry: "2026-03-27",
      ...contractOverrides,
    },
    side: rest.side ?? "BOT",
    quantity: rest.quantity ?? 25,
    avgPrice: rest.avgPrice ?? 5.59,
    commission: rest.commission ?? -1.03,
    realizedPNL: rest.realizedPNL ?? 0,
    time: rest.time ?? "2026-03-17T15:16:13+00:00",
    exchange: rest.exchange ?? "SMART",
    ...rest,
  };
}

function makeBagFill(overrides: Partial<ExecutedOrder> = {}): ExecutedOrder {
  return {
    execId: overrides.execId ?? "bag-fill",
    symbol: overrides.symbol ?? "AAOI",
    contract: {
      conId: 2001,
      symbol: overrides.symbol ?? "AAOI",
      secType: "BAG",
      strike: 0,
      right: "?",
      expiry: null,
    },
    side: overrides.side ?? "BOT",
    quantity: overrides.quantity ?? 25,
    avgPrice: overrides.avgPrice ?? 0.25,
    commission: overrides.commission ?? 0,
    realizedPNL: overrides.realizedPNL ?? null,
    time: overrides.time ?? "2026-03-17T14:32:00+00:00",
    exchange: overrides.exchange ?? "SMART",
  };
}

describe("positionGroupShareData", () => {
  it("ignores unrelated open BAG groups and derives signed entry basis from matching opening legs", () => {
    const unrelatedOpenCombo: PositionFillGroup = {
      id: "open-unrelated-combo",
      symbol: "AAOI",
      description: "Opened AAOI Risk Reversal (Short $92 Call / Long $88 Put)",
      isClosing: false,
      totalQuantity: 25,
      netPrice: 0.25,
      totalCommission: -1.25,
      totalPnL: null,
      time: "2026-03-17T14:01:00+00:00",
      fills: [
        makeBagFill({ execId: "bag-unrelated", avgPrice: 0.25, time: "2026-03-17T14:01:00+00:00" }),
        makeOptionFill({
          execId: "call-unrelated",
          side: "BOT",
          quantity: 25,
          avgPrice: 5.10,
          realizedPNL: null,
          time: "2026-03-17T14:01:00+00:00",
          contract: { conId: 1901, strike: 92, right: "C", expiry: "2026-03-27" },
        }),
        makeOptionFill({
          execId: "put-unrelated",
          side: "SLD",
          quantity: 25,
          avgPrice: 5.35,
          realizedPNL: null,
          time: "2026-03-17T14:01:00+00:00",
          contract: { conId: 1902, strike: 88, right: "P", expiry: "2026-03-27" },
        }),
      ],
    };

    const openCallGroup: PositionFillGroup = {
      id: "open-call",
      symbol: "AAOI",
      description: "Opened AAOI Long Call",
      isClosing: false,
      totalQuantity: 25,
      netPrice: null,
      totalCommission: -17.51,
      totalPnL: null,
      time: "2026-03-17T14:14:16+00:00",
      fills: [
        makeOptionFill({
          execId: "open-call-1",
          side: "BOT",
          quantity: 12,
          avgPrice: 5.59,
          realizedPNL: null,
          time: "2026-03-17T14:14:16+00:00",
          contract: { conId: 861001, strike: 90, right: "C", expiry: "2026-03-27" },
        }),
        makeOptionFill({
          execId: "open-call-2",
          side: "BOT",
          quantity: 13,
          avgPrice: 5.59,
          realizedPNL: null,
          time: "2026-03-17T14:14:16+00:00",
          contract: { conId: 861001, strike: 90, right: "C", expiry: "2026-03-27" },
        }),
      ],
    };

    const openPutGroup: PositionFillGroup = {
      id: "open-put",
      symbol: "AAOI",
      description: "Opened AAOI Short Put",
      isClosing: false,
      totalQuantity: 25,
      netPrice: null,
      totalCommission: -17.53,
      totalPnL: null,
      time: "2026-03-17T14:12:25+00:00",
      fills: [
        makeOptionFill({
          execId: "open-put-1",
          side: "SLD",
          quantity: 13,
          avgPrice: 6.34,
          realizedPNL: null,
          time: "2026-03-17T14:12:25+00:00",
          contract: { conId: 858539, strike: 85, right: "P", expiry: "2026-03-27" },
        }),
        makeOptionFill({
          execId: "open-put-2",
          side: "SLD",
          quantity: 12,
          avgPrice: 6.34,
          realizedPNL: null,
          time: "2026-03-17T14:12:25+00:00",
          contract: { conId: 858539, strike: 85, right: "P", expiry: "2026-03-27" },
        }),
      ],
    };

    const closeGroup: PositionFillGroup = {
      id: "close-rr",
      symbol: "AAOI",
      description: "Closed AAOI Risk Reversal (Short $85 Put / Long $90 Call)",
      isClosing: true,
      totalQuantity: 25,
      netPrice: 1.0,
      totalCommission: -2.06,
      totalPnL: 4337.9,
      time: "2026-03-17T15:16:13+00:00",
      fills: [
        makeBagFill({ execId: "close-bag", avgPrice: 1.0, time: "2026-03-17T15:16:13+00:00" }),
        makeOptionFill({
          execId: "close-call",
          side: "SLD",
          quantity: 25,
          avgPrice: 5.33,
          realizedPNL: 2200,
          time: "2026-03-17T15:16:13+00:00",
          contract: { conId: 861001, strike: 90, right: "C", expiry: "2026-03-27" },
        }),
        makeOptionFill({
          execId: "close-put",
          side: "BOT",
          quantity: 25,
          avgPrice: 7.83,
          realizedPNL: 2137.9,
          time: "2026-03-17T15:16:13+00:00",
          contract: { conId: 858539, strike: 85, right: "P", expiry: "2026-03-27" },
        }),
      ],
    };

    const data = positionGroupShareData(closeGroup, [
      unrelatedOpenCombo,
      openCallGroup,
      openPutGroup,
      closeGroup,
    ]);

    expect(data.entryPrice).toBeCloseTo(-0.75, 2);
    expect(data.exitPrice).toBe(1.0);
    expect(data.pnlPct).toBeCloseTo(231.35, 2);
  });

  // Regression: portfolio-fallback path used legs[0].avg_cost as a per-share
  // entryPrice then re-multiplied entryNotional by 100. Because avg_cost is
  // already per-contract for options, entryNotional was 100× over and pnlPct
  // collapsed toward zero. The fix is to treat avg_cost as per-contract for
  // option legs (divide by 100) when seeding the per-share entryPrice.
  it("uses per-share entry from portfolio fallback for closing options groups", () => {
    const closeGroup: PositionFillGroup = {
      id: "close-usax",
      symbol: "USAX",
      description: "Closed USAX Long Call",
      isClosing: true,
      totalQuantity: 65,
      netPrice: 4.0,
      totalCommission: -32.5,
      totalPnL: 19389.45,
      time: "2026-05-22T14:00:00+00:00",
      fills: [
        makeOptionFill({
          execId: "close-usax-1",
          symbol: "USAX",
          side: "SLD",
          quantity: 65,
          avgPrice: 4.0,
          realizedPNL: 19389.45,
          time: "2026-05-22T14:00:00+00:00",
          contract: { symbol: "USAX", conId: 999001, strike: 45, right: "C", expiry: "2026-06-19" },
        }),
      ],
    };

    const portfolioPosition = {
      id: 1,
      ticker: "USAX",
      structure: "Long Call",
      structure_type: "defined",
      risk_profile: "defined",
      expiry: "2026-06-19",
      contracts: 65,
      direction: "LONG",
      entry_cost: 6630,
      max_risk: 6630,
      market_value: null,
      legs: [
        {
          direction: "LONG" as const,
          contracts: 65,
          type: "Call" as const,
          strike: 45,
          entry_cost: 6630,
          avg_cost: 102,
          market_price: null,
          market_value: null,
        },
      ],
      kelly_optimal: null,
      target: null,
      stop: null,
    };

    const data = positionGroupShareData(closeGroup, [closeGroup], [portfolioPosition]);

    // Without the fix: entryNotional would be 1.02 * 65 * 100 * 100 = $663,000 (100× too big),
    // collapsing pnlPct to ~2.92%. With the fix: 19389.45 / 6630 = 292.45%.
    expect(data.entryPrice).toBeCloseTo(1.02, 2);
    expect(data.exitPrice).toBeCloseTo(4.0, 2);
    expect(data.pnlPct).toBeCloseTo(292.45, 1);
  });
});

/* Regression: MU 2026-06-12 $1000 short call, covered BOT 15 @ $2.00 with
 * realized P&L +$70,441.24. The orders page showed +104.4% (long-only
 * identity entry = exit − pnl applied to a short close) and the share card
 * showed +69.00% (fuzzy portfolio fallback matched the UNRELATED still-open
 * MU Short Call $1050 — 10 contracts, avg_cost $10,209.09 — because the
 * 2-word overlap never compared strikes). Correct short-credit basis:
 * credit received = buy-back cost + realized P&L = $73,441.24 → +95.9%. */
describe("short call buy-to-close return %", () => {
  const realizedPnl = 70441.2398;
  const exitCost = 2.0 * 15 * 100;
  const creditBasis = exitCost + realizedPnl; // credit received at entry
  const expectedPct = (realizedPnl / creditBasis) * 100; // ≈ +95.9

  const muShortCallClose: PositionFillGroup = {
    id: "close-mu-1000c",
    symbol: "MU",
    description: "Closed MU 6/12 (Short $1000 Call)",
    isClosing: true,
    totalQuantity: 15,
    netPrice: 2.0,
    totalCommission: -6.7237,
    totalPnL: realizedPnl,
    time: "2026-06-12T18:57:31+00:00",
    fills: [
      makeOptionFill({
        execId: "close-mu-cover",
        symbol: "MU",
        side: "BOT",
        quantity: 15,
        avgPrice: 2.0,
        realizedPNL: realizedPnl,
        time: "2026-06-12T18:57:31+00:00",
        contract: { symbol: "MU", conId: 879417508, strike: 1000, right: "C", expiry: "2026-06-12" },
      }),
    ],
  };

  const unrelatedOpenShortCall = {
    id: 2,
    ticker: "MU",
    structure: "Short Call $1050.0",
    structure_type: "undefined",
    risk_profile: "undefined",
    expiry: "2026-06-19",
    contracts: 10,
    direction: "SHORT",
    entry_cost: -102090.88,
    max_risk: null,
    market_value: null,
    legs: [
      {
        direction: "SHORT" as const,
        contracts: 10,
        type: "Call" as const,
        strike: 1050,
        entry_cost: -102090.88,
        avg_cost: 10209.088136,
        market_price: null,
        market_value: null,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-05-29",
  };

  it("derives the short-credit basis when no opening fills or portfolio match exist", () => {
    const data = positionGroupShareData(muShortCallClose, [muShortCallClose]);

    expect(data.pnlPct).toBeCloseTo(expectedPct, 6);
    // Entry per-share is the credit received (credits negative per sign convention)
    expect(data.entryPrice).toBeCloseTo(-(creditBasis / 100 / 15), 6);
  });

  it("never borrows basis from a different-strike position on the same underlying", () => {
    const data = positionGroupShareData(muShortCallClose, [muShortCallClose], [unrelatedOpenShortCall]);

    // Pre-fix: fuzzy 2-word overlap matched "Short Call $1050.0" → +69.00%
    expect(data.pnlPct).toBeCloseTo(expectedPct, 6);
    expect(data.entryTime).not.toBe("2026-05-29");
  });

  it("closedGroupReturnPct (orders table cell) agrees with the share card", () => {
    // Pre-fix the orders cell showed +104.4% = pnl / |exit − pnl| for this short
    expect(closedGroupReturnPct(muShortCallClose)).toBeCloseTo(expectedPct, 6);
  });

  it("closedGroupReturnPct keeps long-close math unchanged", () => {
    const longClose: PositionFillGroup = {
      id: "close-long",
      symbol: "AAOI",
      description: "Closed AAOI Long Call",
      isClosing: true,
      totalQuantity: 10,
      netPrice: 3.0,
      totalCommission: -2.0,
      totalPnL: 2000,
      time: "2026-06-12T15:00:00+00:00",
      fills: [
        makeOptionFill({
          execId: "close-long-1",
          side: "SLD",
          quantity: 10,
          avgPrice: 3.0,
          realizedPNL: 2000,
          time: "2026-06-12T15:00:00+00:00",
        }),
      ],
    };
    // Bought 10 @ $1.00 ($1,000 basis), sold @ $3.00 → +200%
    expect(closedGroupReturnPct(longClose)).toBeCloseTo(200, 6);
  });
});
