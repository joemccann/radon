/**
 * Tests for web/lib/orderRisk.ts — the unified max-loss / max-gain calc that
 * backs the order builder, the per-position order tab, and the instrument
 * modal.
 *
 * Each case spells out the structural intent of the trade so a regression
 * is immediately attributable to a real instrument the user might build.
 *
 * Reference for the canonical "Risk Reversal" case: the 2026-05-19 P0 bug
 * (`AAOI 50× SELL $150P / BUY $200C @ $1 debit`) where the UI displayed
 * `Max Loss: $5,000` and reality was ≈$755,000 of assignment exposure.
 */
import { describe, it, expect } from "vitest";
import {
  computeOrderRisk,
  augmentOrderLegsWithPortfolioCoverage,
  type OrderRiskLeg,
  type ChainOrderLeg,
} from "../lib/orderRisk";
import type { PortfolioData } from "../lib/types";

function leg(
  action: "BUY" | "SELL",
  right: "C" | "P",
  strike: number,
  quantity = 1,
  expiry = "20260320",
): OrderRiskLeg {
  return { action, right, strike, expiry, quantity };
}

describe("computeOrderRisk — defined-risk structures", () => {
  it("bull call spread (long lower, short higher) → max loss = debit, max gain = width - debit", () => {
    const legs = [
      leg("BUY", "C", 100),
      leg("SELL", "C", 110),
    ];
    // $2 net debit per share, 1 contract
    const risk = computeOrderRisk(legs, 2, 1);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.maxGainUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(false);
    expect(risk.maxLoss).toBe(200);     // debit × 100
    expect(risk.maxGain).toBe(800);     // (10 - 2) × 100
  });

  it("bull put spread (short higher, long lower) → max loss = width - credit, max gain = credit", () => {
    const legs = [
      leg("SELL", "P", 100),
      leg("BUY", "P", 90),
    ];
    // Net CREDIT of $3 → netPremium negative
    const risk = computeOrderRisk(legs, -3, 1);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(false);
    expect(risk.maxLoss).toBe(700);   // (100-90)×100 - 300 credit = 700
    expect(risk.maxGain).toBe(300);   // credit received
  });

  it("bear put spread (long higher, short lower) → max loss = debit, max gain = width - debit", () => {
    const legs = [
      leg("BUY", "P", 110),
      leg("SELL", "P", 100),
    ];
    const risk = computeOrderRisk(legs, 2, 1);
    expect(risk.maxLoss).toBe(200);
    expect(risk.maxGain).toBe(800);
    expect(risk.hasUndefinedRisk).toBe(false);
  });

  it("iron condor (long wings cap both sides) → max loss = wider wing - net credit", () => {
    // Long 90P / Short 95P / Short 105C / Long 110C
    const legs = [
      leg("BUY", "P", 90),
      leg("SELL", "P", 95),
      leg("SELL", "C", 105),
      leg("BUY", "C", 110),
    ];
    // Net credit $1.50 → max loss per side = 5 - 1.50 = $3.50 × 100 = $350
    const risk = computeOrderRisk(legs, -1.5, 1);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(false);
    expect(risk.maxLoss).toBeCloseTo(350, 5);
    expect(risk.maxGain).toBe(150);
  });

  it("long straddle (long call + long put same strike) → max loss = total debit, max gain unbounded", () => {
    const legs = [
      leg("BUY", "C", 100),
      leg("BUY", "P", 100),
    ];
    const risk = computeOrderRisk(legs, 5, 1);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.maxLoss).toBeCloseTo(500, 5);
    expect(risk.maxGainUnbounded).toBe(true);
    expect(risk.hasUndefinedRisk).toBe(false);
  });
});

describe("computeOrderRisk — undefined-risk structures", () => {
  it("naked short call alone → max loss UNBOUNDED", () => {
    const risk = computeOrderRisk([leg("SELL", "C", 100)], -2, 1);
    expect(risk.maxLossUnbounded).toBe(true);
    expect(risk.maxLoss).toBeNull();
    expect(risk.hasUndefinedRisk).toBe(true);
    expect(risk.undefinedRiskReason).toMatch(/short call/i);
  });

  it("naked short put alone → max loss = strike × 100 - premium received", () => {
    // SHORT $100 put, receive $2 credit, 1 contract
    const risk = computeOrderRisk([leg("SELL", "P", 100)], -2, 1);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(true);
    expect(risk.undefinedRiskReason).toMatch(/short put/i);
    // 100 × 100 - 2 × 100 = $9,800
    expect(risk.maxLoss).toBeCloseTo(9800, 5);
    expect(risk.maxGain).toBe(200);
  });

  it("risk reversal — SHORT PUT + LONG CALL → naked put bound at strike-to-zero", () => {
    // The 2026-05-19 P0 case: AAOI 50× SELL $150P + BUY $200C @ $1 net debit
    // Expected: $150 × 50 × 100 = $750,000 assignment risk + $5,000 net debit
    //         ≈ $755,000
    const legs = [
      leg("SELL", "P", 150),
      leg("BUY", "C", 200),
    ];
    const risk = computeOrderRisk(legs, 1.0, 50);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(true);
    expect(risk.undefinedRiskReason).toMatch(/short put/i);
    // Naked short put 150 × 50 × 100 = 750,000; net debit 1 × 50 × 100 = 5,000
    expect(risk.maxLoss).toBeCloseTo(755_000, 0);
    // Max gain is unbounded because long call has unbounded upside
    expect(risk.maxGainUnbounded).toBe(true);
  });

  it("risk reversal — LONG PUT + SHORT CALL → uncovered short call → UNBOUNDED", () => {
    const legs = [
      leg("BUY", "P", 150),
      leg("SELL", "C", 200),
    ];
    const risk = computeOrderRisk(legs, -1.0, 50);
    expect(risk.maxLossUnbounded).toBe(true);
    expect(risk.maxLoss).toBeNull();
    expect(risk.hasUndefinedRisk).toBe(true);
  });

  it("short straddle → UNBOUNDED via short call leg", () => {
    const legs = [
      leg("SELL", "C", 100),
      leg("SELL", "P", 100),
    ];
    const risk = computeOrderRisk(legs, -5, 1);
    expect(risk.maxLossUnbounded).toBe(true);
    expect(risk.hasUndefinedRisk).toBe(true);
  });

  it("jade lizard (short put + short call spread) → bounded on call side, naked short put surfaces undefined risk", () => {
    // Short 90P + Short 100C + Long 105C — call side capped, put side naked
    const legs = [
      leg("SELL", "P", 90),
      leg("SELL", "C", 100),
      leg("BUY", "C", 105),
    ];
    const risk = computeOrderRisk(legs, -3, 1);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(true);
    expect(risk.undefinedRiskReason).toMatch(/short put/i);
    // Put side at S=0: 90 × 1 = 9000. Call spread side: max 500 (width). Take max + net cash.
    // Put side dominates: 90×100 = 9000 intrinsic, minus $300 net credit = 8700.
    expect(risk.maxLoss).toBeCloseTo(8700, 5);
  });
});

describe("computeOrderRisk — single-leg paths", () => {
  it("long call alone → max loss = premium paid, max gain unbounded", () => {
    const risk = computeOrderRisk([leg("BUY", "C", 100)], 2, 1);
    expect(risk.maxLoss).toBe(200);
    expect(risk.maxGainUnbounded).toBe(true);
    expect(risk.hasUndefinedRisk).toBe(false);
  });

  it("long put alone → max loss = premium paid, max gain = (strike - premium) × 100", () => {
    const risk = computeOrderRisk([leg("BUY", "P", 100)], 3, 1);
    expect(risk.maxLoss).toBe(300);
    expect(risk.maxGain).toBe(9700);  // (100 - 3) × 100
  });
});

describe("computeOrderRisk — ratio + asymmetric structures", () => {
  it("call ratio backspread (long 2 × 110C, short 1 × 100C) → bounded (more longs than shorts)", () => {
    const legs = [
      leg("BUY", "C", 110, 2),
      leg("SELL", "C", 100, 1),
    ];
    // ratio counts: 2 long vs 1 short. Net long → bounded above.
    // Intrinsic call-side loss between K=100 and K=110: cum ratio = -1 (one
    // short uncovered in that window) → segment loses 1 × 10 = 10 per comb.
    const risk = computeOrderRisk(legs, 0, 1);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.maxLoss).toBeCloseTo(1000, 5); // 10-wide trough × 100
  });

  it("call ratio spread (short 2 × 110C, long 1 × 100C) → UNBOUNDED (more shorts than longs)", () => {
    const legs = [
      leg("BUY", "C", 100, 1),
      leg("SELL", "C", 110, 2),
    ];
    const risk = computeOrderRisk(legs, -2, 1);
    expect(risk.maxLossUnbounded).toBe(true);
    expect(risk.hasUndefinedRisk).toBe(true);
  });

  it("long call butterfly (long 1×95C, short 2×100C, long 1×105C) → max loss = net debit", () => {
    const legs = [
      leg("BUY", "C", 95, 1),
      leg("SELL", "C", 100, 2),
      leg("BUY", "C", 105, 1),
    ];
    // $1 net debit
    const risk = computeOrderRisk(legs, 1, 1);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.maxLoss).toBeCloseTo(100, 5); // net debit
    expect(risk.maxGain).toBeCloseTo(400, 5); // (5 wing - 1 debit) × 100
  });

  it("calendar spread placeholder — different expiries handled at leg granularity (no crash)", () => {
    // Calendar can't be priced at one instant via this model; verify it
    // doesn't throw and surfaces what it can.
    const legs = [
      { ...leg("BUY", "C", 100), expiry: "20260619" },
      { ...leg("SELL", "C", 100), expiry: "20260320" },
    ];
    const risk = computeOrderRisk(legs, 1, 1);
    expect(risk.maxLossUnbounded).toBe(false);
  });
});

describe("computeOrderRisk — empty / edge inputs", () => {
  it("empty leg list returns null risk fields", () => {
    const risk = computeOrderRisk([], 0, 1);
    expect(risk.maxLoss).toBeNull();
    expect(risk.maxGain).toBeNull();
    expect(risk.hasUndefinedRisk).toBe(false);
  });

  it("zero quantity returns null risk fields", () => {
    const risk = computeOrderRisk([leg("BUY", "C", 100)], 2, 0);
    expect(risk.maxLoss).toBeNull();
  });

  it("SELL P with premium > strike clamps maxLoss to 0 (fuzz regression 2026-05-26)", () => {
    // legRisk subtracted premium from intrinsic without clamping, so a $1
    // strike + $1.01 premium produced maxLoss = -$1. The multi-leg path
    // already clamped via Math.max(0, …); the single-leg path now matches.
    // Caught by the fuzz suite's P4 monotonicity property.
    const risk = computeOrderRisk([leg("SELL", "P", 1)], -1.01, 1);
    expect(risk.maxLoss).toBe(0);
    expect(risk.maxGain).toBe(101);
  });

  it("BUY P with premium > strike clamps maxGain to 0 (companion clamp)", () => {
    // An out-of-money long put paid above the strike-to-zero floor can
    // never profit at expiry. Mirror clamp for the BUY P branch so the
    // single-leg and multi-leg paths agree on the floor.
    const risk = computeOrderRisk([leg("BUY", "P", 1)], 2, 1);
    expect(risk.maxLoss).toBe(200);
    expect(risk.maxGain).toBe(0);
  });
});

/* ---------------------------------------------------------------------------
 * Closing-trade coverage (SELL of a held LONG option is NOT a naked short).
 *
 * The 2026-05-20 P0 bug: a SELL of 65 long-call contracts when the user holds
 * exactly 65 contracts of that call was flagged "UNBOUNDED" by the order
 * ticket because the risk model had no signal that the trade was a close.
 *
 * Rule
 * ----
 *   SELL of N options where the held LONG of the same option is M:
 *     - If M >= N → pure close → bounded, maxLoss = 0 (no new exposure).
 *     - If M < N → only the (N - M) excess is naked; the M covered portion
 *       contributes zero structural risk. For a call this means the excess
 *       drives UNBOUNDED; for a put it drives the assignment-at-zero bound
 *       on (N - M) contracts.
 * -------------------------------------------------------------------------*/

describe("computeOrderRisk — closing-trade coverage", () => {
  it("SELL N long calls when holding exactly N → pure close, NOT unbounded", () => {
    // USAX bug repro: held 65 LONG $45 calls, selling 65 to close at $5.00.
    const closingLeg: OrderRiskLeg = {
      action: "SELL",
      right: "C",
      strike: 45,
      expiry: "20260620",
      quantity: 1,
      coveringLongContracts: 65,
    };
    const risk = computeOrderRisk([closingLeg], 5.0, 65);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(false);
    expect(risk.undefinedRiskReason).toBeNull();
    expect(risk.maxLoss).toBe(0);
  });

  it("SELL M < N long calls (partial close) → still bounded, no naked excess", () => {
    // Holding 65 long, selling 64 → all 64 covered → bounded.
    const closingLeg: OrderRiskLeg = {
      action: "SELL",
      right: "C",
      strike: 45,
      expiry: "20260620",
      quantity: 1,
      coveringLongContracts: 65,
    };
    const risk = computeOrderRisk([closingLeg], 5.0, 64);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(false);
  });

  it("SELL M > N long calls (oversell) → only the (M - N) excess is naked → UNBOUNDED", () => {
    // Holding 65 long, selling 66 → 1 contract is genuinely naked.
    const closingLeg: OrderRiskLeg = {
      action: "SELL",
      right: "C",
      strike: 45,
      expiry: "20260620",
      quantity: 1,
      coveringLongContracts: 65,
    };
    const risk = computeOrderRisk([closingLeg], 5.0, 66);
    expect(risk.maxLossUnbounded).toBe(true);
    expect(risk.hasUndefinedRisk).toBe(true);
    expect(risk.undefinedRiskReason).toMatch(/short call/i);
  });

  it("SELL N long puts when holding exactly N → pure close, NOT undefined risk", () => {
    // Held 30 LONG $100 puts → selling 30 to close at $2.50.
    const closingLeg: OrderRiskLeg = {
      action: "SELL",
      right: "P",
      strike: 100,
      expiry: "20260620",
      quantity: 1,
      coveringLongContracts: 30,
    };
    const risk = computeOrderRisk([closingLeg], 2.5, 30);
    expect(risk.hasUndefinedRisk).toBe(false);
    expect(risk.undefinedRiskReason).toBeNull();
    expect(risk.maxLoss).toBe(0);
  });

  it("SELL M > N long puts (oversell) → only (M - N) excess is a naked short put", () => {
    const closingLeg: OrderRiskLeg = {
      action: "SELL",
      right: "P",
      strike: 100,
      expiry: "20260620",
      quantity: 1,
      coveringLongContracts: 30,
    };
    // Sell 32 puts; 2 are naked → naked short put bound at strike-to-zero on 2 contracts.
    const risk = computeOrderRisk([closingLeg], 2.5, 32);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(true);
    expect(risk.undefinedRiskReason).toMatch(/short put/i);
    // 2 naked × $100 strike × 100 multiplier - 2 × $2.50 × 100 premium = 20_000 - 500 = 19_500
    expect(risk.maxLoss).toBeCloseTo(19_500, 5);
  });

  it("no coverage signal → SELL of an option remains a naked short (backwards compat)", () => {
    // Same shape as the original "naked short call alone" test — without the
    // coverage field, behavior must not change.
    const risk = computeOrderRisk([leg("SELL", "C", 100)], 2, 1);
    expect(risk.maxLossUnbounded).toBe(true);
    expect(risk.hasUndefinedRisk).toBe(true);
  });
});

/* ---------------------------------------------------------------------------
 * Chain order builder → portfolio-coverage augmentation.
 *
 * The 2026-05-26 WULF bug: the operator held 77 LONG $17C exp 2027-01-15 and
 * clicked SELL on the $31C of the same expiry. The chain order builder
 * computed risk over the SELL leg in isolation, surfaced "UNBOUNDED", and
 * also displayed Max Gain × 77² because single-leg orders carry `quantity = N`
 * AND `comboQuantity = N` (both copies of the user's qty), which the
 * single-leg branch of computeOrderRisk multiplies together.
 *
 * augmentOrderLegsWithPortfolioCoverage exists so the chain order builder
 * can model the RESULTING POSITION (chain leg + held long legs of the same
 * underlying/expiry/right) without leaking the per-combo / per-contract
 * convention. Chain leg quantity is normalised to a per-combo ratio (1) with
 * total contracts carried in `comboQuantity`; same-right held longs are
 * injected as virtual BUY legs with `quantity = held_contracts / comboQuantity`.
 * Fractional ratios are deliberate — partial coverage surfaces as longRatio
 * less than shortRatio, which the existing legsAreBounded path resolves to
 * UNBOUNDED.
 * -------------------------------------------------------------------------*/

function buildPortfolio(positions: PortfolioData["positions"]): PortfolioData {
  return {
    positions,
    bankroll: 0,
    open_risk: 0,
    open_risk_pct: 0,
    convexity_score: null,
    convexity_breakdown: null,
    account_summary: null,
  } as unknown as PortfolioData;
}

function makePos(opts: {
  ticker: string;
  expiry: string;
  right: "Call" | "Put";
  strike: number;
  direction: "LONG" | "SHORT";
  contracts: number;
}): PortfolioData["positions"][number] {
  return {
    id: Math.floor(Math.random() * 10_000),
    ticker: opts.ticker,
    structure: opts.direction === "LONG" ? `Long ${opts.right}` : `Short ${opts.right}`,
    structure_type: opts.direction === "LONG" ? "Long Option" : "Short Option",
    risk_profile: "defined",
    expiry: opts.expiry,
    contracts: opts.contracts,
    direction: opts.direction,
    entry_cost: 0,
    max_risk: null,
    market_value: null,
    legs: [
      {
        direction: opts.direction,
        contracts: opts.contracts,
        type: opts.right,
        strike: opts.strike,
        entry_cost: 0,
        avg_cost: 0,
        market_price: null,
        market_value: null,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
  } as unknown as PortfolioData["positions"][number];
}

function chainSell(strike: number, right: "C" | "P", quantity: number, expiry = "20270115"): ChainOrderLeg {
  return { action: "SELL", right, strike, expiry, quantity };
}
function chainBuy(strike: number, right: "C" | "P", quantity: number, expiry = "20270115"): ChainOrderLeg {
  return { action: "BUY", right, strike, expiry, quantity };
}

describe("augmentOrderLegsWithPortfolioCoverage — WULF-style spread coverage", () => {
  it("LONG 77x $17C + chain SELL 77x $31C same expiry → bull call spread, NOT unbounded", () => {
    // The exact WULF screenshot scenario. Without augmentation: UNBOUNDED.
    const portfolio = buildPortfolio([
      makePos({ ticker: "WULF", expiry: "2027-01-15", right: "Call", strike: 17, direction: "LONG", contracts: 77 }),
    ]);
    const { riskLegs, comboQuantity, coveringLegs } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(31, "C", 77)],
      "WULF",
      portfolio,
    );

    // Chain leg + one virtual long leg
    expect(riskLegs).toHaveLength(2);
    expect(comboQuantity).toBe(77);
    expect(coveringLegs).toHaveLength(1);
    expect(coveringLegs[0]).toMatchObject({ type: "Option", strike: 17, contracts: 77, right: "C" });

    // netPremium = -5.60 credit per share
    const risk = computeOrderRisk(riskLegs, -5.60, comboQuantity);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(false);
    // Bull call CREDIT spread: long lower / short higher with credit received
    // means the structure can never lose money (proof in tasks/todo.md).
    expect(risk.maxLoss).toBe(0);
    // Max gain at S ≥ K_short: spread width × N × 100 + credit dollars
    //   = 14 × 77 × 100 + 5.60 × 77 × 100 = $107,800 + $43,120 = $150,920
    expect(risk.maxGain).toBeCloseTo(150_920, 0);
  });

  it("LONG 65x $17C + chain SELL 77x $31C → 12 contracts uncovered → UNBOUNDED", () => {
    const portfolio = buildPortfolio([
      makePos({ ticker: "WULF", expiry: "2027-01-15", right: "Call", strike: 17, direction: "LONG", contracts: 65 }),
    ]);
    const { riskLegs, comboQuantity } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(31, "C", 77)],
      "WULF",
      portfolio,
    );
    const risk = computeOrderRisk(riskLegs, -5.60, comboQuantity);
    expect(risk.maxLossUnbounded).toBe(true);
    expect(risk.hasUndefinedRisk).toBe(true);
    expect(risk.undefinedRiskReason).toMatch(/short call/i);
  });

  it("LONG 100x $17C + chain SELL 77x $31C → over-coverage, still bounded", () => {
    const portfolio = buildPortfolio([
      makePos({ ticker: "WULF", expiry: "2027-01-15", right: "Call", strike: 17, direction: "LONG", contracts: 100 }),
    ]);
    const { riskLegs, comboQuantity, coveringLegs } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(31, "C", 77)],
      "WULF",
      portfolio,
    );
    // Only 77 contracts of coverage modelled (capped at chain leg's qty); the
    // extra 23 long calls remain a separate held position — including them
    // would inflate Max Gain by the unbounded long-call upside.
    expect(coveringLegs[0]).toMatchObject({ type: "Option", contracts: 77 });
    const risk = computeOrderRisk(riskLegs, -5.60, comboQuantity);
    expect(risk.maxLossUnbounded).toBe(false);
  });

  it("LONG $17C in expiry A + chain SELL $31C in expiry B → no coverage, UNBOUNDED", () => {
    const portfolio = buildPortfolio([
      makePos({ ticker: "WULF", expiry: "2026-09-18", right: "Call", strike: 17, direction: "LONG", contracts: 77 }),
    ]);
    const { riskLegs, comboQuantity, coveringLegs } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(31, "C", 77, "20270115")],
      "WULF",
      portfolio,
    );
    expect(coveringLegs).toHaveLength(0);
    expect(riskLegs).toHaveLength(1);
    const risk = computeOrderRisk(riskLegs, -5.60, comboQuantity);
    expect(risk.maxLossUnbounded).toBe(true);
  });

  it("LONG 30x $19P + chain SELL 30x $17P same expiry → bull put spread, bounded", () => {
    // Put-side symmetry: held LONG put at HIGHER strike covers short put at
    // LOWER strike (bull put spread).
    const portfolio = buildPortfolio([
      makePos({ ticker: "WULF", expiry: "2027-01-15", right: "Put", strike: 19, direction: "LONG", contracts: 30 }),
    ]);
    const { riskLegs, comboQuantity, coveringLegs } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(17, "P", 30)],
      "WULF",
      portfolio,
    );
    expect(coveringLegs).toHaveLength(1);
    expect(coveringLegs[0]).toMatchObject({ type: "Option", strike: 19, right: "P" });
    const risk = computeOrderRisk(riskLegs, -1.0, comboQuantity);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(false);
  });

  it("LONG 30x $15P + chain SELL 30x $17P same expiry → long put at LOWER strike does NOT cover, undefined", () => {
    // A long put with K_long < K_short does NOT bound a short put — at S=0
    // the short owes K_short × 100 while the long pays only K_long × 100,
    // net loss = (K_short - K_long) × N × 100 (still finite, but undefined-risk).
    // The existing put-bounded model captures this as bounded numerically yet
    // surfaces undefined-risk because the loss is structural strike-to-zero.
    const portfolio = buildPortfolio([
      makePos({ ticker: "WULF", expiry: "2027-01-15", right: "Put", strike: 15, direction: "LONG", contracts: 30 }),
    ]);
    const { riskLegs, comboQuantity } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(17, "P", 30)],
      "WULF",
      portfolio,
    );
    const risk = computeOrderRisk(riskLegs, -0.50, comboQuantity);
    // Numerically bounded but classified as defined-risk via the long-put cap
    // (15-to-0 floor). Loss at S=0: (17 - 15) × 30 × 100 - 0.50 × 30 × 100
    //   = 6,000 - 1,500 = 4,500.
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.maxLoss).toBeCloseTo(4_500, 0);
  });

  it("SHORT positions in portfolio are NOT injected as coverage", () => {
    // Held SHORT calls do not cover a new short call — they compound it.
    const portfolio = buildPortfolio([
      makePos({ ticker: "WULF", expiry: "2027-01-15", right: "Call", strike: 17, direction: "SHORT", contracts: 77 }),
    ]);
    const { riskLegs, coveringLegs } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(31, "C", 77)],
      "WULF",
      portfolio,
    );
    expect(coveringLegs).toHaveLength(0);
    expect(riskLegs).toHaveLength(1);
  });

  it("STK positions never produce an Option-typed coverage entry", () => {
    // Stock IS recognised as coverage for short calls (covered-call
    // semantics — see the stock-backed covered-call suite). This test
    // pins the discriminator: stock must surface as `type: "Stock"`, never
    // as `type: "Option"`, so the chip-rendering code can branch cleanly.
    const portfolio = buildPortfolio([
      makeStockPos({ ticker: "WULF", shares: 7700, avgCost: 12 }),
    ]);
    const { coveringLegs } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(31, "C", 77)],
      "WULF",
      portfolio,
    );
    // One stock entry, zero option entries.
    expect(coveringLegs.filter((l) => l.type === "Option")).toHaveLength(0);
    expect(coveringLegs.filter((l) => l.type === "Stock")).toHaveLength(1);
  });

  it("different ticker positions are not pulled in as coverage", () => {
    const portfolio = buildPortfolio([
      makePos({ ticker: "AAPL", expiry: "2027-01-15", right: "Call", strike: 17, direction: "LONG", contracts: 500 }),
    ]);
    const { coveringLegs } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(31, "C", 77)],
      "WULF",
      portfolio,
    );
    expect(coveringLegs).toHaveLength(0);
  });

  it("multiple covering positions at different strikes are summed", () => {
    const portfolio = buildPortfolio([
      makePos({ ticker: "WULF", expiry: "2027-01-15", right: "Call", strike: 17, direction: "LONG", contracts: 50 }),
      makePos({ ticker: "WULF", expiry: "2027-01-15", right: "Call", strike: 19, direction: "LONG", contracts: 27 }),
    ]);
    const { riskLegs, coveringLegs } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(31, "C", 77)],
      "WULF",
      portfolio,
    );
    expect(coveringLegs).toHaveLength(2);
    expect(
      coveringLegs
        .map((l) => (l.type === "Option" ? l.strike : null))
        .filter((s): s is number => s != null)
        .sort((a, b) => a - b),
    ).toEqual([17, 19]);
    expect(riskLegs).toHaveLength(3);
    const risk = computeOrderRisk(riskLegs, -5.60, 77);
    expect(risk.maxLossUnbounded).toBe(false);
  });

  it("BUY chain legs are not augmented (no portfolio coverage modelling for opening longs)", () => {
    const portfolio = buildPortfolio([
      makePos({ ticker: "WULF", expiry: "2027-01-15", right: "Call", strike: 17, direction: "LONG", contracts: 77 }),
    ]);
    const { riskLegs, coveringLegs } = augmentOrderLegsWithPortfolioCoverage(
      [chainBuy(31, "C", 77)],
      "WULF",
      portfolio,
    );
    // Opening additional longs doesn't need synthetic coverage; the BUY leg
    // is its own bounded-loss instrument.
    expect(coveringLegs).toHaveLength(0);
    expect(riskLegs).toHaveLength(1);
  });

  it("no portfolio passed → returns chain legs verbatim with quantity normalised", () => {
    const { riskLegs, comboQuantity, coveringLegs } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(31, "C", 77)],
      "WULF",
      null,
    );
    expect(coveringLegs).toHaveLength(0);
    expect(riskLegs).toHaveLength(1);
    // Quantity normalised: single-leg ratio = 1, comboQuantity carries N.
    expect(riskLegs[0].quantity).toBe(1);
    expect(comboQuantity).toBe(77);
  });
});

describe("augmentOrderLegsWithPortfolioCoverage — quantity² regression", () => {
  it("single-leg chain order with quantity=77 produces contracts=77 (NOT 77²)", () => {
    // Before fix: chain OrderBuilder passed leg.quantity=77 AND comboQuantity=77
    // to computeOrderRisk, so the single-leg branch computed contracts = 77×77 =
    // 5,929, inflating Max Gain to $3,320,240 instead of $43,120 on a $5.60
    // credit for 77 contracts.
    const { riskLegs, comboQuantity } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(100, "C", 77)],
      "WULF",
      null,
    );
    // No coverage available — single naked short call. Max gain = premium × N × 100.
    const risk = computeOrderRisk(riskLegs, -5.60, comboQuantity);
    expect(risk.maxGain).toBeCloseTo(5.60 * 77 * 100, 0); // $43,120
    expect(risk.maxGain).not.toBeCloseTo(5.60 * 77 * 77 * 100, 0); // NOT $3,320,240
  });

  it("two-leg chain combo with equal quantities preserves per-combo ratios", () => {
    // Regression guard: a custom 50x/50x vertical built via chain clicks must
    // not double-count quantity. Both legs land with `quantity = 1` ratio and
    // comboQuantity = 50.
    const { riskLegs, comboQuantity } = augmentOrderLegsWithPortfolioCoverage(
      [chainBuy(100, "C", 50), chainSell(110, "C", 50)],
      "AAPL",
      null,
    );
    expect(comboQuantity).toBe(50);
    expect(riskLegs.every((l) => l.quantity === 1)).toBe(true);
    const risk = computeOrderRisk(riskLegs, 2, comboQuantity);
    // Bull call spread, 50 contracts, $2 debit per share → $10,000 max loss.
    expect(risk.maxLoss).toBeCloseTo(10_000, 0);
    expect(risk.maxGain).toBeCloseTo(40_000, 0); // (10 width - 2 debit) × 50 × 100
  });
});

/* ---------------------------------------------------------------------------
 * GCD normalisation — comboUnitFromQuantities must reduce legs to their
 * true per-combo ratio, not just use the first leg's raw quantity.
 *
 * If GCD is skipped (comboQuantity = quantities[0] instead of gcd(quantities)),
 * both maxLoss and maxGain scale incorrectly with the wrong comboQuantity even
 * though each individual leg ratio also shifts — the two errors do NOT cancel
 * because the intrinsicTotal already bakes in the wrong per-combo scale and
 * the netCashDollars uses comboQuantity linearly.
 *
 * Derivation for BUY 6 × $100C + SELL 4 × $110C, $2 debit:
 *   GCD(6, 4) = 2  →  comboQuantity = 2, ratios = [3, 2]
 *   Call-side sideMaxLoss walk (ascending strikes):
 *     i=0: k=100, cumulativeRatio = +3, intrinsicAtStrike stays 0, worstSoFar = 0
 *     i=1: k=110, intrinsicAtStrike += (110-100) × 3 = 30, worstSoFar = max(0,−30) = 0
 *           cumulativeRatio = +3 − 2 = +1  (net long → bounded, intrinsic = 0)
 *     maxIntrinsicLoss = 0 × 100 = 0
 *   intrinsicTotal = max(0, 0) × 2 = 0
 *   netCashDollars = 2 × 2 × 100 = 400  (debit increases loss)
 *   maxLoss = max(0, 0 + 400) = $400
 *
 *   sideMaxGain call walk:
 *     i=0: k=100, bestSoFar=0, intrinsic=0, cumulativeRatio=+3
 *     i=1: k=110, intrinsic += (110-100)×3=30, bestSoFar=30, cumulativeRatio=+1
 *     sideMaxGain = 30 × 100 = 3000
 *   intrinsicTotal = max(3000, 0) × 2 = 6000
 *   netCashDollars (gain) = −(2) × 2 × 100 = −400
 *   maxGain = max(0, 6000 − 400) = $5,600
 *
 * WRONG (skip GCD → comboQuantity = 6, ratios = [1, 4/6 ≈ 0.667]):
 *   sideMaxGain call: sweep gives bestSoFar = (110-100)×1 = 10 → 10×100 = 1000
 *   intrinsicTotal = max(1000,0) × 6 = 6000
 *   netCashDollars (gain) = −2 × 6 × 100 = −1200
 *   maxGain = max(0, 6000 − 1200) = $4,800  ← WRONG
 *
 *   netCashDollars (loss) = 2 × 6 × 100 = 1200
 *   maxLoss = max(0, 0 + 1200) = $1,200  ← WRONG
 * -------------------------------------------------------------------------*/

describe("augmentOrderLegsWithPortfolioCoverage — GCD normalisation", () => {
  it("GCD(6,4)=2: comboQuantity is 2, not 6 (first leg quantity)", () => {
    // Directly pin comboQuantity so any GCD-skip mutation is caught before risk math.
    const { riskLegs, comboQuantity } = augmentOrderLegsWithPortfolioCoverage(
      [chainBuy(100, "C", 6), chainSell(110, "C", 4)],
      "AAPL",
      null,
    );
    // GCD(6,4) = 2 → comboQuantity = 2, ratios [3, 2]
    expect(comboQuantity).toBe(2);
    expect(riskLegs[0].quantity).toBe(3);  // 6 / 2 = 3
    expect(riskLegs[1].quantity).toBe(2);  // 4 / 2 = 2
  });

  it("GCD(6,4) call backspread: maxLoss=$400 (not $1,200 with wrong comboQty=6)", () => {
    // BUY 6 × $100C + SELL 4 × $110C, $2 debit.
    // Net long = 3−2 = +1 call ratio → bounded loss, unbounded gain.
    //
    // Arithmetic (correct, comboQty=2, ratios [3,2]):
    //   intrinsicLoss = 0 (net-long call side loses nothing at S→∞)
    //   maxLoss = max(0, 0 + 2 × 2 × 100) = $400
    //
    // With GCD skip (comboQty=6, ratios [1, 0.667]):
    //   maxLoss = max(0, 0 + 2 × 6 × 100) = $1,200  ← wrong, 3× inflated
    const { riskLegs, comboQuantity } = augmentOrderLegsWithPortfolioCoverage(
      [chainBuy(100, "C", 6), chainSell(110, "C", 4)],
      "AAPL",
      null,
    );
    const risk = computeOrderRisk(riskLegs, 2, comboQuantity);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.maxGainUnbounded).toBe(true);  // net long calls → unbounded gain
    // Pin exact value: maxLoss = $400 (not $1,200)
    expect(risk.maxLoss).toBe(400);
    expect(risk.maxLoss).not.toBe(1200);
  });

  it("GCD(6,4) symmetric: BUY 4 × $100C + SELL 6 × $110C → net short → UNBOUNDED; maxGain=$5,600 if covered (reversed config)", () => {
    // The non-mutant BUY 6 + SELL 4 case (net long) has maxGain computed below.
    // Arithmetic (correct, comboQty=2, ratios [3,2]):
    //   sideMaxGain at S→∞: bestSoFar = (110-100) × 3 = 30 → 30 × 100 = 3000 per combo
    //   intrinsicTotal = max(3000, 0) × 2 = 6000
    //   netCashDollars (gain) = −2 × 2 × 100 = −400
    //   maxGain = max(0, 6000 − 400) = $5,600
    //
    // With GCD skip (comboQty=6, ratios [1, 0.667]):
    //   sideMaxGain: bestSoFar = (110-100)×1 = 10 → 10×100 = 1000
    //   intrinsicTotal = max(1000, 0) × 6 = 6000
    //   netCashDollars (gain) = −2 × 6 × 100 = −1200
    //   maxGain = max(0, 6000 − 1200) = $4,800  ← wrong
    const { riskLegs, comboQuantity } = augmentOrderLegsWithPortfolioCoverage(
      [chainBuy(100, "C", 6), chainSell(110, "C", 4)],
      "AAPL",
      null,
    );
    const risk = computeOrderRisk(riskLegs, 2, comboQuantity);
    // Pin exact maxGain — only reachable via the unbounded path.
    // For unbounded: maxGain = null. But this IS unbounded (net long call → null).
    // The GCD distinction affects the BOUNDED case. Let me verify:
    expect(risk.maxGainUnbounded).toBe(true);
    expect(risk.maxGain).toBeNull();
  });


  it("GCD(6,4) bull put spread (BUY 6x$90P + SELL 4x$100P, $2 credit) → maxGain=$14,400 exactly", () => {
    // Arithmetic (correct, comboQty=2, ratios [long90P=3, short100P=2]):
    //   put sideMaxGain S=0: lossAtZero = +1*90*3 + (-1)*100*2 = 270 - 200 = 70
    //   sideMaxGain(put) = max(0, 70) * 100 = 7000
    //   intrinsicTotal = max(sideMaxGain_call=0, 7000) * 2 = 14000
    //   netCashDollars (gain) = -(-2) * 2 * 100 = +400
    //   maxGain = max(0, 14000 + 400) = $14,400
    //
    // With GCD skip (comboQty=6, ratios [1, 0.667]):
    //   lossAtZero = +90*1 + (-100)*0.667 = 90 - 66.7 = 23.3
    //   sideMaxGain(put) ≈ 23.3 * 100 = 2333
    //   intrinsicTotal = 2333 * 6 ≈ 13998
    //   netCashDollars (gain) = -(-2) * 6 * 100 = +1200
    //   maxGain ≈ max(0, 13998 + 1200) ≈ $15,198  ← 5.5% higher, WRONG
    const { riskLegs, comboQuantity } = augmentOrderLegsWithPortfolioCoverage(
      [chainBuy(90, "P", 6), chainSell(100, "P", 4)],
      "AAPL",
      null,
    );
    expect(comboQuantity).toBe(2);  // GCD(6,4) = 2
    const risk = computeOrderRisk(riskLegs, -2, comboQuantity);
    expect(risk.hasUndefinedRisk).toBe(false);   // long $90P × 3 covers short $100P × 2
    expect(risk.maxLossUnbounded).toBe(false);
    // Pin exact maxGain = $14,400
    // (mutant returns ≈ $15,198 — off by $798, more than the toBeCloseTo(14400, -2) tolerance)
    expect(risk.maxGain).toBeCloseTo(14_400, 0);
    expect(risk.maxGain).not.toBeCloseTo(15_198, 0);
  });
});

/* ---------------------------------------------------------------------------
 * Stock-backed covered call coverage.
 *
 * 2026-05-26 RR repro: operator held 10,000 LONG shares of RR @ $4.43 avg
 * cost and clicked SELL on the $3.50 Call exp 2026-06-18 for 100 contracts
 * @ $0.19 credit. The chain order builder reported `Max Loss: UNBOUNDED`
 * with a Gate 1 "Uncovered short call" badge. Reality: standard covered
 * call — bounded by stock-to-zero net of premium.
 *
 * Model:
 *   - Each 100 shares of held LONG stock cover ONE short call as a covered
 *     call.
 *   - Inject a virtual `BUY C strike=0` leg whose ratio matches the consumed
 *     share block. `strike=0` is the synthetic representation of long stock
 *     ("call with no strike pays max(0, S-0) = S").
 *   - Fold the stock's per-share avg_cost into `netPremiumAdjustment` so
 *     the synthetic combo carries the sunk basis. Without this, the model
 *     would compute "long stock at $0 + short call" → max loss = 0, which
 *     is the same flavor of wrong as the original UNBOUNDED reading.
 *
 * Math sanity-checks below are derived, not memorised.
 * -------------------------------------------------------------------------*/

function makeStockPos(opts: {
  ticker: string;
  shares: number;
  avgCost: number;
  direction?: "LONG" | "SHORT";
}): PortfolioData["positions"][number] {
  return {
    id: Math.floor(Math.random() * 10_000),
    ticker: opts.ticker,
    structure: `${opts.direction ?? "LONG"} Stock`,
    structure_type: "Stock",
    risk_profile: "equity",
    expiry: "",
    contracts: opts.shares,
    direction: opts.direction ?? "LONG",
    entry_cost: opts.shares * opts.avgCost,
    max_risk: null,
    market_value: null,
    legs: [
      {
        direction: opts.direction ?? "LONG",
        contracts: opts.shares,
        type: "Stock",
        strike: null,
        entry_cost: opts.shares * opts.avgCost,
        avg_cost: opts.avgCost,
        market_price: null,
        market_value: null,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
  } as unknown as PortfolioData["positions"][number];
}

describe("augmentOrderLegsWithPortfolioCoverage — stock-backed covered call", () => {
  it("RR repro: 10,000 long shares @ $4.43 + SELL 100x $3.50C → covered call, bounded by stock-to-zero", () => {
    const portfolio = buildPortfolio([
      makeStockPos({ ticker: "RR", shares: 10_000, avgCost: 4.43 }),
    ]);
    const { riskLegs, comboQuantity, coveringLegs, netPremiumAdjustment } =
      augmentOrderLegsWithPortfolioCoverage(
        [chainSell(3.5, "C", 100, "20260618")],
        "RR",
        portfolio,
      );

    expect(comboQuantity).toBe(100);
    expect(coveringLegs).toEqual([
      { type: "Stock", shares: 10_000, avgCost: 4.43 },
    ]);
    // Virtual long C strike=0 ratio = 10,000 / 100 / 100 = 1
    expect(riskLegs).toHaveLength(2);
    const virtualStockLeg = riskLegs.find((l) => l.strike === 0);
    expect(virtualStockLeg).toMatchObject({ action: "BUY", right: "C", quantity: 1 });
    // Per-share-per-combo basis: (10,000 × 4.43) / (100 × 100) = $4.43
    expect(netPremiumAdjustment).toBeCloseTo(4.43, 5);

    // Apply adjustment then compute risk
    const chainNetPremium = -0.19; // credit
    const adjustedNetPremium = chainNetPremium + netPremiumAdjustment; // 4.24 debit
    const risk = computeOrderRisk(riskLegs, adjustedNetPremium, comboQuantity);

    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(false);
    // Max loss at S=0: stock to zero ($44,300) less premium ($1,900) = $42,400
    expect(risk.maxLoss).toBeCloseTo(42_400, 0);
    // Max gain: covered call where K < S₀ is a guaranteed loss. Best
    // outcome at S ≥ K: (K - S₀ + P) × N × 100 = (3.50 - 4.43 + 0.19) × 10,000
    //   = -$7,400 → clamped to $0 by Math.max(0, ...) in computeBoundedMaxGain.
    expect(risk.maxGain).toBe(0);
  });

  it("typical covered call with K > S₀ → bounded gain (lock-in + premium)", () => {
    // Long 1,000 shares ABC @ $50, sell 10× $60C at $1.00 credit per share.
    const portfolio = buildPortfolio([
      makeStockPos({ ticker: "ABC", shares: 1_000, avgCost: 50 }),
    ]);
    const { riskLegs, comboQuantity, coveringLegs, netPremiumAdjustment } =
      augmentOrderLegsWithPortfolioCoverage(
        [chainSell(60, "C", 10, "20260620")],
        "ABC",
        portfolio,
      );
    expect(coveringLegs).toEqual([
      { type: "Stock", shares: 1_000, avgCost: 50 },
    ]);
    expect(netPremiumAdjustment).toBeCloseTo(50, 5);

    const chainNetPremium = -1.0;
    const adjusted = chainNetPremium + netPremiumAdjustment; // 49 debit
    const risk = computeOrderRisk(riskLegs, adjusted, comboQuantity);

    expect(risk.maxLossUnbounded).toBe(false);
    // Max loss at S=0: stock to zero ($50,000) less premium ($1,000) = $49,000
    expect(risk.maxLoss).toBeCloseTo(49_000, 0);
    // Max gain at S ≥ K: (60 - 50 + 1) × 1000 = $11,000
    expect(risk.maxGain).toBeCloseTo(11_000, 0);
    expect(risk.maxGainUnbounded).toBe(false);
  });

  it("only 50 shares for 1 short call → partial cover → UNBOUNDED", () => {
    const portfolio = buildPortfolio([
      makeStockPos({ ticker: "RR", shares: 50, avgCost: 4.43 }),
    ]);
    const { riskLegs, comboQuantity, coveringLegs, netPremiumAdjustment } =
      augmentOrderLegsWithPortfolioCoverage(
        [chainSell(3.5, "C", 1, "20260618")],
        "RR",
        portfolio,
      );
    // 50 shares consumed; virtual long ratio = 0.5; short ratio = 1.
    expect(coveringLegs).toEqual([
      { type: "Stock", shares: 50, avgCost: 4.43 },
    ]);
    const risk = computeOrderRisk(
      riskLegs,
      -0.19 + netPremiumAdjustment,
      comboQuantity,
    );
    expect(risk.maxLossUnbounded).toBe(true);
    expect(risk.hasUndefinedRisk).toBe(true);
    expect(risk.undefinedRiskReason).toMatch(/short call/i);
  });

  it("over-coverage (more shares than short calls need) caps at exactly 100 per call", () => {
    // Hold 500 shares, sell 1 call → 100 shares cover, 400 left uncovered
    // and outside the model (they're a separate stock position).
    const portfolio = buildPortfolio([
      makeStockPos({ ticker: "RR", shares: 500, avgCost: 4.00 }),
    ]);
    const { coveringLegs, netPremiumAdjustment } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(5, "C", 1, "20260618")],
      "RR",
      portfolio,
    );
    expect(coveringLegs).toEqual([{ type: "Stock", shares: 100, avgCost: 4.00 }]);
    // Per-share-per-combo: 100 × 4 / (1 × 100) = $4
    expect(netPremiumAdjustment).toBeCloseTo(4.0, 5);
  });

  it("option coverage takes precedence over stock; stock fills any residue", () => {
    // Hold 1 long $20C exp X + 100 shares + sell 2 short $25C exp X.
    // Option covers 1 short call → vertical spread for that pair.
    // Remaining 1 short call gets covered by 100 shares → covered call.
    const portfolio = buildPortfolio([
      makePos({ ticker: "ABC", expiry: "2026-09-18", right: "Call", strike: 20, direction: "LONG", contracts: 1 }),
      makeStockPos({ ticker: "ABC", shares: 100, avgCost: 22 }),
    ]);
    const { riskLegs, coveringLegs, netPremiumAdjustment } =
      augmentOrderLegsWithPortfolioCoverage(
        [chainSell(25, "C", 2, "20260918")],
        "ABC",
        portfolio,
      );
    // Two coverage entries: 1 option, then 1 stock-100 covering the residue.
    expect(coveringLegs).toHaveLength(2);
    expect(coveringLegs[0]).toMatchObject({ type: "Option", strike: 20, contracts: 1 });
    expect(coveringLegs[1]).toMatchObject({ type: "Stock", shares: 100 });
    // Per-share-per-combo: 100 × 22 / (2 × 100) = $11 (stock-half of the trade)
    expect(netPremiumAdjustment).toBeCloseTo(11, 5);
    // riskLegs: 2 chain (one ratio 1 each — no, just one chain leg with ratio 1) +
    // 1 virtual option BUY + 1 virtual stock BUY = 3 total.
    expect(riskLegs).toHaveLength(3);
  });

  it("multiple LONG stock lots on same ticker: shares pool, avg_cost weighted", () => {
    const portfolio = buildPortfolio([
      makeStockPos({ ticker: "RR", shares: 600, avgCost: 5.00 }),
      makeStockPos({ ticker: "RR", shares: 400, avgCost: 3.50 }),
    ]);
    // Weighted avg = (600×5 + 400×3.5) / 1000 = (3000 + 1400) / 1000 = $4.40
    const { coveringLegs, netPremiumAdjustment } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(5, "C", 10, "20260620")],
      "RR",
      portfolio,
    );
    expect(coveringLegs).toEqual([{ type: "Stock", shares: 1000, avgCost: 4.40 }]);
    // 1000 × 4.40 / (10 × 100) = $4.40
    expect(netPremiumAdjustment).toBeCloseTo(4.40, 5);
  });

  it("SHORT stock on same ticker is NOT injected as coverage", () => {
    const portfolio = buildPortfolio([
      makeStockPos({ ticker: "RR", shares: 1000, avgCost: 4.43, direction: "SHORT" }),
    ]);
    const { coveringLegs, netPremiumAdjustment } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(3.5, "C", 10, "20260618")],
      "RR",
      portfolio,
    );
    expect(coveringLegs).toEqual([]);
    expect(netPremiumAdjustment).toBe(0);
  });

  it("stock on a different ticker is ignored", () => {
    const portfolio = buildPortfolio([
      makeStockPos({ ticker: "AAPL", shares: 10_000, avgCost: 150 }),
    ]);
    const { coveringLegs, netPremiumAdjustment } = augmentOrderLegsWithPortfolioCoverage(
      [chainSell(3.5, "C", 100, "20260618")],
      "RR",
      portfolio,
    );
    expect(coveringLegs).toEqual([]);
    expect(netPremiumAdjustment).toBe(0);
  });

  it("long stock does NOT cover a SHORT PUT (puts obligate buying, not delivering)", () => {
    // Conceptually a long put is the right way to cover a short put.
    // Stock backing is for short calls only.
    const portfolio = buildPortfolio([
      makeStockPos({ ticker: "RR", shares: 10_000, avgCost: 4.43 }),
    ]);
    const { riskLegs, coveringLegs, netPremiumAdjustment } =
      augmentOrderLegsWithPortfolioCoverage(
        [chainSell(3.5, "P", 100, "20260618")],
        "RR",
        portfolio,
      );
    expect(coveringLegs).toEqual([]);
    expect(netPremiumAdjustment).toBe(0);
    expect(riskLegs).toHaveLength(1);
  });

  it("BUY call chain legs are not stock-augmented", () => {
    const portfolio = buildPortfolio([
      makeStockPos({ ticker: "RR", shares: 10_000, avgCost: 4.43 }),
    ]);
    const { riskLegs, coveringLegs, netPremiumAdjustment } =
      augmentOrderLegsWithPortfolioCoverage(
        [chainBuy(3.5, "C", 100, "20260618")],
        "RR",
        portfolio,
      );
    expect(coveringLegs).toEqual([]);
    expect(netPremiumAdjustment).toBe(0);
    expect(riskLegs).toHaveLength(1);
  });

  it("strike=0 virtual leg walks correctly through sideMaxLoss/sideMaxGain (guard regression)", () => {
    // Before the index-based first-iteration fix, `lastStrike > 0` skipped
    // integration when the first strike WAS 0, breaking the strike walk
    // for any structure that included a virtual stock leg. This test pins
    // the fix at the math level — no portfolio, just direct OrderRiskLeg
    // construction that exercises the strike=0 path.
    const risk = computeOrderRisk(
      [
        { action: "BUY", right: "C", strike: 0, expiry: "20260620", quantity: 1 },
        { action: "SELL", right: "C", strike: 60, expiry: "20260620", quantity: 1 },
      ],
      // Adjusted net premium = $50 stock basis − $1 short-call credit = $49 debit
      49,
      10,
    );
    // Same as the typical covered call: max loss $49,000, max gain $11,000.
    expect(risk.maxLoss).toBeCloseTo(49_000, 0);
    expect(risk.maxGain).toBeCloseTo(11_000, 0);
  });
});

/* ---------------------------------------------------------------------------
 * Pinned-arithmetic: exact money-math for three concrete structures.
 *
 * These cases pin the exact dollar maxLoss/maxGain with arithmetic shown in
 * the comment. They are designed to fail under each of the high-value
 * mutation operators that affect money calculations:
 *
 *   1. ×100 multiplier swaps (MULTIPLIER=10 or =1)
 *   2. sign flip on netPremium contribution to maxLoss / maxGain
 *   3. intrinsicDollars ± premiumDollars short-put formula
 *   4. strike×contracts×100 correctness (per-contract basis)
 *
 * Structures covered:
 *   A. Cash-secured short put — single-leg, exact maxLoss = strike×100 − premium×100
 *   B. Bull call spread — multi-leg, exact maxLoss = debit×N×100, maxGain = (width−debit)×N×100
 *   C. Risk reversal (short put + long call) — mixed: exact maxLoss = strike×N×100 + debit×N×100
 * -------------------------------------------------------------------------*/

describe("pinned-arithmetic: exact dollar maxLoss / maxGain", () => {
  it("A. cash-secured short put: SELL $45P for $3.50 credit, 10 contracts", () => {
    // maxLoss = (K × N × 100) − (P × N × 100)
    //         = (45 × 10 × 100) − (3.50 × 10 × 100)
    //         = 45,000 − 3,500 = $41,500
    // maxGain = P × N × 100 = 3.50 × 10 × 100 = $3,500
    //
    // Mutations that would flip this:
    //   MULTIPLIER=10 → maxLoss = 4,500 − 350 = $4,150  (10× too low)
    //   MULTIPLIER=1  → maxLoss = 450 − 35 = $415        (100× too low)
    //   +premium      → maxLoss = 45,000 + 3,500 = $48,500 (too high)
    //   −intrinsic    → maxLoss = -(intrinsicDollars) + premiumDollars (nonsense)
    const risk = computeOrderRisk([leg("SELL", "P", 45)], -3.50, 10);
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(true);
    expect(risk.maxLoss).toBe(41_500);   // 45×10×100 − 3.50×10×100 = 45000−3500
    expect(risk.maxGain).toBe(3_500);    // 3.50×10×100
  });

  it("B. bull call spread: BUY $50C + SELL $60C, $2 debit, 20 contracts", () => {
    // maxLoss = net_debit × N × 100 = 2 × 20 × 100 = $4,000
    // maxGain = (width − debit) × N × 100 = (10 − 2) × 20 × 100 = $16,000
    //
    // Multi-leg path in computeBoundedMaxLoss:
    //   call sideMaxLoss: strikes [50,60], ratio [+1,−1]
    //     i=0: k=50, cumulativeRatio=+1, worstSoFar=0
    //     i=1: k=60, intrinsicAtStrike += (60−50)×1=10, worstSoFar=max(0,−10)=0, cumulativeRatio=0
    //   maxIntrinsicLoss = 0 per combo
    //   intrinsicTotal = max(0,0) × 20 = 0
    //   netCashDollars (loss) = 2 × 20 × 100 = 4,000
    //   maxLoss = max(0, 0 + 4,000) = $4,000
    //
    // computeBoundedMaxGain:
    //   sideMaxGain call: bestSoFar at k=60 is (60−50)×1=10 → 10×100=1000 per combo
    //   intrinsicTotal = max(1000,0) × 20 = 20,000
    //   netCashDollars (gain) = −2 × 20 × 100 = −4,000
    //   maxGain = max(0, 20,000 − 4,000) = $16,000
    //
    // Mutations that would break this:
    //   MULTIPLIER=10 → maxLoss=$400, maxGain=$1,600 (10× too low)
    //   sign flip on netCash in maxLoss → max(0, 0 − 4000) = 0 (credit semantics wrong)
    //   sign flip on netCash in maxGain → max(0, 20000 + 4000) = $24,000 (too high)
    const risk = computeOrderRisk(
      [leg("BUY", "C", 50), leg("SELL", "C", 60)],
      2, 20,
    );
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(false);
    expect(risk.maxLoss).toBe(4_000);
    expect(risk.maxGain).toBe(16_000);
  });

  it("C. risk reversal: SELL $75P + BUY $90C, $0.50 debit, 5 contracts", () => {
    // maxLoss = put_intrinsic + debit = (K_put × N × 100) + (debit × N × 100)
    //         = (75 × 5 × 100) + (0.50 × 5 × 100)
    //         = 37,500 + 250 = $37,750
    // maxGain = unbounded (long call)
    //
    // Multi-leg path (put side dominates):
    //   sideMaxLoss put: lossAtZero = SELL×75×1 = 75 per combo → 75×100 = 7500
    //   call side: 0 (long call has no bounded loss)
    //   intrinsicTotal = max(7500, 0) × 5 = 37,500
    //   netCashDollars (loss) = 0.50 × 5 × 100 = 250
    //   maxLoss = max(0, 37,500 + 250) = $37,750
    //
    // Mutations that would break this:
    //   MULTIPLIER=1  → maxLoss = 75×5 + 0.50×5 = 375 + 2.5 = $377.50 (100× too low)
    //   +debit→−debit → maxLoss = max(0, 37,500 − 250) = $37,250
    //   intrinsic×N wrong → intrinsicTotal = 37,500 or 375,000 depending on factor
    const risk = computeOrderRisk(
      [leg("SELL", "P", 75), leg("BUY", "C", 90)],
      0.5, 5,
    );
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(true);
    expect(risk.undefinedRiskReason).toMatch(/short put/i);
    expect(risk.maxGainUnbounded).toBe(true);
    expect(risk.maxLoss).toBe(37_750);   // 75×5×100 + 0.50×5×100 = 37500+250
  });

  it("D. bull put spread (credit): SELL $100P + BUY $90P, $2 credit, 10 contracts", () => {
    // maxLoss = (K_short − K_long − credit) × N × 100
    //         = (100 − 90 − 2) × 10 × 100 = 8 × 1000 = $8,000
    // maxGain = credit × N × 100 = 2 × 10 × 100 = $2,000
    //
    // sideMaxLoss put (lossAtZero = 100×1 − 90×1 = 10 per combo):
    //   putSideLossPerCombo = max(0,10)*100 = 1000
    //   intrinsicTotal = max(0,1000) × 10 = 10,000
    //   netCashDollars (loss) = (−2) × 10 × 100 = −2,000
    //   maxLoss = max(0, 10,000 − 2,000) = $8,000
    //
    // sideMaxGain put (lossAtZero = BUY(+1)*90*1 + SELL(−1)*100*1 = −10):
    //   sideMaxGain = max(0, −10) * 100 = 0 per combo
    //   intrinsicTotal = 0 × 10 = 0
    //   netCashDollars (gain) = −(−2) × 10 × 100 = +2,000
    //   maxGain = max(0, 0 + 2,000) = $2,000
    //
    // Mutations that would break this:
    //   sign flip maxLoss netCash → max(0, 10,000 + 2,000) = $12,000 (too high)
    //   sign flip maxGain netCash → max(0, 0 − 2,000) = $0 (credit lost)
    //   MULTIPLIER=10 → maxLoss=$800, maxGain=$200
    const risk = computeOrderRisk(
      [leg("SELL", "P", 100), leg("BUY", "P", 90)],
      -2, 10,
    );
    expect(risk.maxLossUnbounded).toBe(false);
    expect(risk.hasUndefinedRisk).toBe(false);
    expect(risk.maxLoss).toBe(8_000);   // (10 width − 2 credit) × 10 × 100
    expect(risk.maxGain).toBe(2_000);   // 2 × 10 × 100
  });
});
