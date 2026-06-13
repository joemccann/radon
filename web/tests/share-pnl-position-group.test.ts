import { describe, expect, it } from "vitest";
import { closedGroupReturnPct, positionGroupShareData, type PositionFillGroup } from "../components/WorkspaceSections";
import type { ExecutedOrder, PortfolioPosition } from "../lib/types";

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

/* ── Mutation-test strengthening — kill survivors found by manual analysis ──
 *
 * Each describe block targets a specific surviving mutant identified by the
 * mutation testing session (2026-06-13). Arithmetic is derived from first
 * principles and shown inline so a wrong assertion cannot hide a real bug.
 */

describe("multi-leg portfolio fallback — direction sign must be: LONG = paid (−1), SHORT = received (+1)", () => {
  /* Bull Call Spread: Long $90 Call / Short $95 Call, 10 contracts.
   *
   * avg_cost is per-CONTRACT for options (already ×100):
   *   LONG $90 Call  avg_cost = 800  → per-share = 800 / 100 = $8.00
   *   SHORT $95 Call avg_cost = 300  → per-share = 300 / 100 = $3.00
   *
   * Net entry price (per share, sign convention: paid = negative):
   *   sign(LONG)  = −1  →  −1 × 8.00 = −8.00
   *   sign(SHORT) = +1  →  +1 × 3.00 = +3.00
   *   netCost = −8.00 + 3.00 = −5.00  (net debit)
   *
   * Mutating LONG→+1 / SHORT→−1 gives netCost = +8.00 − 3.00 = +5.00 (wrong sign).
   * The test pins entryPrice < 0 (debit position) to kill that mutant.
   *
   * entryNotional = |−5.00| × 10 contracts × 100 = $5,000
   * pnlPct        = 2,500 / 5,000 × 100 = +50%
   */
  const totalPnL = 2500;
  const longAvgCostPerContract = 800;
  const shortAvgCostPerContract = 300;
  const contracts = 10;
  const multiplier = 100;
  const longPerShare = longAvgCostPerContract / multiplier;    // $8.00
  const shortPerShare = shortAvgCostPerContract / multiplier;  // $3.00
  const netCost = -longPerShare + shortPerShare;               // −5.00 (debit)
  const entryNotional = Math.abs(netCost) * contracts * multiplier; // $5,000
  const expectedPct = (totalPnL / entryNotional) * 100;       // +50%

  const closeGroup: PositionFillGroup = {
    id: "close-bcs",
    symbol: "AAOI",
    description: "Closed AAOI Bull Call Spread (Long $90 / Short $95)",
    isClosing: true,
    totalQuantity: contracts,
    netPrice: 2.50,
    totalCommission: -5.00,
    totalPnL,
    time: "2026-06-13T15:00:00+00:00",
    fills: [
      makeOptionFill({
        execId: "close-long-leg",
        side: "BOT",
        quantity: contracts,
        avgPrice: 3.0,
        realizedPNL: totalPnL,
        time: "2026-06-13T15:00:00+00:00",
        contract: { conId: 990001, strike: 90, right: "C", expiry: "2026-06-20" },
      }),
    ],
  };

  const portfolioCombo: PortfolioPosition = {
    id: 10,
    ticker: "AAOI",
    structure: "Bull Call Spread",
    structure_type: "defined",
    risk_profile: "defined",
    expiry: "2026-06-20",
    contracts,
    direction: "LONG",
    entry_cost: entryNotional,
    max_risk: entryNotional,
    market_value: null,
    legs: [
      {
        direction: "LONG",
        contracts,
        type: "Call",
        strike: 90,
        entry_cost: longAvgCostPerContract * contracts,
        avg_cost: longAvgCostPerContract,
        market_price: null,
        market_value: null,
      },
      {
        direction: "SHORT",
        contracts,
        type: "Call",
        strike: 95,
        entry_cost: -(shortAvgCostPerContract * contracts),
        avg_cost: shortAvgCostPerContract,
        market_price: null,
        market_value: null,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-06-01",
  };

  it("multi-leg net cost uses LONG=paid(−1) / SHORT=received(+1) sign convention", () => {
    const data = positionGroupShareData(closeGroup, [closeGroup], [portfolioCombo]);

    // entryPrice must be negative — a net debit position was paid for
    // Mutation M14 (sign flip: LONG→+1, SHORT→−1) gives +5.00 (wrong)
    expect(data.entryPrice).toBeCloseTo(netCost, 6);   // ≈ −5.00
    expect(data.entryPrice).toBeLessThan(0);

    // pnlPct = 2500 / 5000 × 100 = +50.00%
    // Mutation M14 leaves |entryNotional| the same → pnlPct is still 50%,
    // BUT entryPrice sign is wrong. Pin both so any sign-flip on either leg is caught.
    expect(data.pnlPct).toBeCloseTo(expectedPct, 4);  // +50.00%
    expect(data.entryTime).toBe("2026-06-01");
  });
});

describe("closedGroupCloseCash — side='SELL' alias must equal side='SLD'", () => {
  /* The production cashSign guard accepts `fill.side === "SLD" || fill.side === "SELL"`.
   * Mutating this to `fill.side === "SLD"` (dropping the SELL alias) makes any fill
   * with side="SELL" return cashSign=0 → closedGroupCloseCash returns null
   * → closedGroupReturnPct returns null (wrong — should be +200%).
   *
   * SLD 5 contracts @ $3.00, pnl = $1,000
   *   closeCash = +1 × 3.00 × 5 × 100 = +$1,500   (received cash)
   *   openCash  = 1,000 − 1,500 = −$500             (paid at entry)
   *   pnlPct    = 1,000 / |−500| × 100 = +200%
   *
   * Same arithmetic applies when side="SELL" (the IB alias). Mutation M18 drops
   * the alias, producing null instead of +200%.
   */
  const qty = 5;
  const closePrice = 3.00;
  const pnl = 1000;
  // closeCash = +1 × 3.00 × 5 × 100 = 1,500
  const closeCash = closePrice * qty * 100;
  // openCash = 1,000 − 1,500 = −500
  const openCash = pnl - closeCash;
  // pnlPct = 1,000 / 500 × 100 = 200%
  const expectedPct = (pnl / Math.abs(openCash)) * 100;

  const groupWithSellAlias: PositionFillGroup = {
    id: "close-sell-alias",
    symbol: "NVDA",
    description: "Closed NVDA Long Call (SELL alias)",
    isClosing: true,
    totalQuantity: qty,
    netPrice: closePrice,
    totalCommission: -1.25,
    totalPnL: pnl,
    time: "2026-06-13T14:00:00+00:00",
    fills: [
      makeOptionFill({
        execId: "close-sell-1",
        symbol: "NVDA",
        side: "SELL",   // IB alias for SLD — must give cashSign = +1
        quantity: qty,
        avgPrice: closePrice,
        realizedPNL: pnl,
        time: "2026-06-13T14:00:00+00:00",
        contract: { conId: 880001, symbol: "NVDA", secType: "OPT", strike: 150, right: "C", expiry: "2026-07-18" },
      }),
    ],
  };

  it("treats side='SELL' identically to side='SLD' when computing closedGroupReturnPct", () => {
    // Mutation M18 drops the SELL alias → cashSign=0 → returns null
    expect(closedGroupReturnPct(groupWithSellAlias)).toBeCloseTo(expectedPct, 6);  // +200%
  });

  it("treats side='BUY' identically to side='BOT' for a buy-to-close group", () => {
    /* BOT / BUY = paid cash → cashSign = −1
     * BOT 5 @ $3.00, pnl = $1,000 (short, bought to cover)
     *   closeCash = −1 × 3.00 × 5 × 100 = −$1,500   (paid to close)
     *   openCash  = 1,000 − (−1,500) = +$1,500        (credit received at open)
     *   pnlPct    = 1,000 / 1,500 × 100 ≈ +66.67%
     */
    const shortPnl = 1000;
    const shortClosePrice = 3.00;
    const shortCloseCash = -shortClosePrice * qty * 100;  // −1,500
    const shortOpenCash = shortPnl - shortCloseCash;       // +1,500
    const shortExpectedPct = (shortPnl / Math.abs(shortOpenCash)) * 100;  // 66.67%

    const groupWithBuyAlias: PositionFillGroup = {
      id: "close-buy-alias",
      symbol: "NVDA",
      description: "Closed NVDA Short Call (BUY alias)",
      isClosing: true,
      totalQuantity: qty,
      netPrice: shortClosePrice,
      totalCommission: -1.25,
      totalPnL: shortPnl,
      time: "2026-06-13T14:30:00+00:00",
      fills: [
        makeOptionFill({
          execId: "close-buy-1",
          symbol: "NVDA",
          side: "BUY",   // IB alias for BOT — must give cashSign = −1
          quantity: qty,
          avgPrice: shortClosePrice,
          realizedPNL: shortPnl,
          time: "2026-06-13T14:30:00+00:00",
          contract: { conId: 880002, symbol: "NVDA", secType: "OPT", strike: 150, right: "C", expiry: "2026-07-18" },
        }),
      ],
    };

    expect(closedGroupReturnPct(groupWithBuyAlias)).toBeCloseTo(shortExpectedPct, 6);
  });
});

describe("positionGroupShareData — isClosing gate: non-closing groups must yield null pnlPct", () => {
  /* Mutation M26 drops `&& group.isClosing` from the main P&L guard.
   * If an opening group has totalPnL = 0 (non-null), the mutated code enters
   * the P&L branch and computes openCash from the opening fills, yielding
   * pnlPct = 0 / |openCash| × 100 = 0 instead of null.
   *
   * Input: opening BOT group, 10 contracts @ $2.00, totalPnL = 0 (not null)
   *   isClosing = false  ← the critical flag
   *   fills: BOT 10 @ $2.00 (opening position)
   *
   * Correct: pnlPct must be null (not yet closed)
   * M26 mutation: openCash = 0 − (−2.00×10×100) = +2,000 → pnlPct = 0%
   */
  const openingGroup: PositionFillGroup = {
    id: "open-bcs",
    symbol: "SPY",
    description: "Opened SPY Long Put",
    isClosing: false,  // opening position — must NOT compute P&L %
    totalQuantity: 10,
    netPrice: 2.00,
    totalCommission: -2.50,
    totalPnL: 0,  // zero but non-null (triggers M26 if isClosing check is dropped)
    time: "2026-06-13T10:00:00+00:00",
    fills: [
      makeOptionFill({
        execId: "open-spy-put",
        symbol: "SPY",
        side: "BOT",
        quantity: 10,
        avgPrice: 2.00,
        realizedPNL: 0,
        time: "2026-06-13T10:00:00+00:00",
        contract: { conId: 770001, symbol: "SPY", secType: "OPT", strike: 500, right: "P", expiry: "2026-07-18" },
      }),
    ],
  };

  it("returns null pnlPct for a non-closing group even when totalPnL is 0 (not null)", () => {
    // Mutation M26 drops `&& group.isClosing` → pnlPct becomes 0, not null
    const data = positionGroupShareData(openingGroup, [openingGroup], []);
    expect(data.pnlPct).toBeNull();
    // entryPrice and exitPrice must also be null for opening groups
    expect(data.entryPrice).toBeNull();
    expect(data.exitPrice).toBeNull();
  });
});

describe("portfolio fuzzy-match word-overlap threshold — requires >= 2 words, not >= 1", () => {
  /* Mutation M15 weakens `overlap.length >= 2` to `>= 1`, allowing a position
   * with only 1 matching word to become the entry basis. The strike check runs
   * first and must pass for this to be a problem, so we construct a case where:
   *  - strikes match (close group strike 200 = portfolio leg strike 200)
   *  - only 1 word overlaps between the close description and the portfolio structure
   *
   * Close description: "Closed NVDA Naked Call"       words: [closed, nvda, naked, call]
   * Portfolio structure: "Bull Call Spread"            words: [bull, call, spread]
   * Overlap: ["call"] — exactly 1 word.
   *
   * With >= 2: no match → falls through to P&L identity.
   * With >= 1: matches → uses portfolio avg_cost ($500/contract = $5/share) as
   *            entryPrice, yielding entryNotional = 5.00 × 5 × 100 = $2,500,
   *            pnlPct = 1,500 / 2,500 × 100 = +60% (WRONG).
   *
   * Correct via P&L identity:
   *   closeCash = +1 × 4.00 × 5 × 100 = +$2,000  (SLD = received)
   *   openCash  = 1,500 − 2,000 = −$500            (paid at entry)
   *   pnlPct    = 1,500 / 500 × 100 = +300%        (CORRECT)
   */
  const qty = 5;
  const closePricePerShare = 4.00;
  const pnl = 1500;
  // P&L identity:
  const closeCash = closePricePerShare * qty * 100;   // +$2,000
  const openCash = pnl - closeCash;                   // −$500
  const correctPct = (pnl / Math.abs(openCash)) * 100; // +300%

  // Wrong pnlPct if 1-word match accidentally picks up the unrelated portfolio position
  const portfolioAvgCostPerContract = 500;
  const wrongEntryNotional = (portfolioAvgCostPerContract / 100) * qty * 100; // $2,500
  const wrongPct = (pnl / wrongEntryNotional) * 100; // +60%

  const closeGroup: PositionFillGroup = {
    id: "close-naked-call",
    symbol: "NVDA",
    description: "Closed NVDA Naked Call",
    isClosing: true,
    totalQuantity: qty,
    netPrice: closePricePerShare,
    totalCommission: -1.25,
    totalPnL: pnl,
    time: "2026-06-13T16:00:00+00:00",
    fills: [
      makeOptionFill({
        execId: "close-naked-1",
        symbol: "NVDA",
        side: "SLD",
        quantity: qty,
        avgPrice: closePricePerShare,
        realizedPNL: pnl,
        time: "2026-06-13T16:00:00+00:00",
        contract: { conId: 990201, symbol: "NVDA", secType: "OPT", strike: 200, right: "C", expiry: "2026-07-18" },
      }),
    ],
  };

  // Portfolio has a Bull Call Spread on NVDA $200 Call — same strike, only 1 word overlap
  const unrelatedBullSpread: PortfolioPosition = {
    id: 20,
    ticker: "NVDA",
    structure: "Bull Call Spread",
    structure_type: "defined",
    risk_profile: "defined",
    expiry: "2026-07-18",
    contracts: qty,
    direction: "LONG",
    entry_cost: wrongEntryNotional,
    max_risk: wrongEntryNotional,
    market_value: null,
    legs: [
      {
        direction: "LONG",
        contracts: qty,
        type: "Call",
        strike: 200,  // same strike as close group
        entry_cost: portfolioAvgCostPerContract * qty,
        avg_cost: portfolioAvgCostPerContract,
        market_price: null,
        market_value: null,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-05-15",
  };

  it("requires >= 2 overlapping words: 1-word match 'call' does not hijack entry basis", () => {
    const data = positionGroupShareData(closeGroup, [closeGroup], [unrelatedBullSpread]);

    // M15 mutation (>= 1) would pick up 'call' overlap → pnlPct = +60% (wrong)
    // Correct via P&L identity: pnlPct = 1,500 / 500 × 100 = +300%
    expect(data.pnlPct).toBeCloseTo(correctPct, 4);      // +300%
    expect(data.pnlPct).not.toBeCloseTo(wrongPct, 0);    // must NOT be ≈ 60%
    // entryTime must not come from the unrelated spread (entry_date: 2026-05-15)
    expect(data.entryTime).not.toBe("2026-05-15");
  });
});
