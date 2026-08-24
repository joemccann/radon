/**
 * @vitest-environment node
 *
 * REL-060 / R-146 (P1): `isNetDebitPaid` publishes a Return % on a position
 * whose risk it does not bound.
 *
 * 3b5d05ff replaced `isFullLossDebit` — which admitted only
 * `risk_profile === "defined"`, a single long stock leg, or an all-long
 * option position — with `isNetDebitPaid`, which admits ANY position whose
 * `resolveEntryCost` is positive except a single-leg short. There is no
 * risk-profile check on the new path. A 1x2 call ratio (buy 1 @ $10, sell
 * 2 @ $4.50) nets +$100 of debit and carries UNBOUNDED upside risk, so a
 * deliberate "Return unavailable: no verified capital basis" became a
 * four-digit percentage labelled "Return on debit paid · exact" on the
 * position table, the ticker-detail tab and mobile. The label is true of the
 * debit and false of the risk it is presented as a return on.
 */
import { describe, it, expect } from "vitest";

import { resolveReturnCapital, describeReturnCapital } from "../lib/positionUtils";
import type { PortfolioPosition } from "../lib/types";

function position(
  legs: Array<{
    direction: "LONG" | "SHORT";
    right?: "C" | "P";
    contracts: number;
    entry_cost: number;
  }>,
  overrides: Partial<PortfolioPosition> = {},
): PortfolioPosition {
  return {
    ticker: "AAPL",
    structure: "test",
    structure_type: "Complex",
    risk_profile: "undefined",
    contracts: 1,
    entry_date: "2026-08-01",
    entry_cost: legs.reduce((a, l) => a + l.entry_cost, 0),
    max_risk: null,
    legs: legs.map((l, i) => ({
      direction: l.direction,
      type: (l.right ?? "C") === "P" ? "Put" : "Call",
      strike: 100 + i * 5,
      expiry: "20261218",
      contracts: l.contracts,
      entry_cost: l.entry_cost,
      avg_cost: l.entry_cost / Math.max(1, l.contracts),
    })),
    ...overrides,
  } as unknown as PortfolioPosition;
}

describe("resolveReturnCapital refuses an uncovered short", () => {
  it("gives no basis to a 1x2 call ratio", () => {
    // buy 1 @ $10 = +$1,000, sell 2 @ $4.50 = -$900 → net debit +$100,
    // and the second short call is naked.
    const ratio = position([
      { direction: "LONG", right: "C", contracts: 1, entry_cost: 1000 },
      { direction: "SHORT", right: "C", contracts: 2, entry_cost: -900 },
    ]);

    const basis = resolveReturnCapital(ratio);

    expect(basis).toBeNull();
    expect(describeReturnCapital(basis)).toContain("no verified capital basis");
  });

  it("gives no basis to a debit risk reversal with a naked put", () => {
    const rr = position([
      { direction: "LONG", right: "C", contracts: 1, entry_cost: 800 },
      { direction: "SHORT", right: "P", contracts: 1, entry_cost: -700 },
    ]);
    expect(resolveReturnCapital(rr)).toBeNull();
  });

  it("keeps the basis when every short is covered by a same-right long", () => {
    // A 1x1 debit call spread: the short call is covered.
    const spread = position([
      { direction: "LONG", right: "C", contracts: 1, entry_cost: 1000 },
      { direction: "SHORT", right: "C", contracts: 1, entry_cost: -400 },
    ]);
    const basis = resolveReturnCapital(spread);
    expect(basis?.kind).toBe("debit-paid");
    expect(basis?.amount).toBe(600);
  });

  it("keeps the basis for an all-long position", () => {
    const straddle = position([
      { direction: "LONG", right: "C", contracts: 1, entry_cost: 500 },
      { direction: "LONG", right: "P", contracts: 1, entry_cost: 400 },
    ]);
    expect(resolveReturnCapital(straddle)?.kind).toBe("debit-paid");
  });

  it("still prefers an exact defined-risk max loss", () => {
    const defined = position(
      [
        { direction: "LONG", right: "C", contracts: 1, entry_cost: 1000 },
        { direction: "SHORT", right: "C", contracts: 2, entry_cost: -900 },
      ],
      { risk_profile: "defined", max_risk: 250 } as Partial<PortfolioPosition>,
    );
    expect(resolveReturnCapital(defined)?.kind).toBe("max-risk");
  });
});

/**
 * REL-060 / R-147 (web half): `twr.cum_return` and `twr.annualized` are
 * plausibility-gated; `mwr.period_return` and `mwr.annualized` were not, so
 * the MWR/IRR card could render an absurd or sign-inverted figure beside a
 * TWR the same payload had correctly suppressed.
 */
describe("MWR is plausibility-gated like TWR", () => {
  it("suppresses an absurd MWR that arrives with a suppressed TWR", async () => {
    const { buildPerformanceView } = await import("../lib/performanceData");
    const view = buildPerformanceView({
      schema_version: 2,
      status: "ok",
      as_of: "2026-08-22",
      calendar_days: 30,
      n_returns: 60,
      twr: { cum_return: 9.51, annualized: { value: null, n: 60, min_n: 20 } },
      mwr: {
        period_return: { value: 9.51, n: 60, min_n: 20 },
        annualized: { value: 120.0, n: 60, min_n: 20 },
      },
      equity: {},
      flows: { status: "ok" },
    } as never);

    expect(view.twrCumReturn).toBeNull();
    expect(view.mwrPeriod.value).toBeNull();
    expect(view.mwrAnnualized.value).toBeNull();
  });

  it("keeps an ordinary MWR", async () => {
    const { buildPerformanceView } = await import("../lib/performanceData");
    const view = buildPerformanceView({
      schema_version: 2,
      status: "ok",
      as_of: "2026-08-22",
      calendar_days: 400,
      n_returns: 300,
      twr: { cum_return: 0.12, annualized: { value: 0.11, n: 300, min_n: 20 } },
      mwr: {
        period_return: { value: 0.13, n: 300, min_n: 20 },
        annualized: { value: 0.12, n: 300, min_n: 20 },
      },
      equity: {},
      flows: { status: "ok" },
    } as never);

    expect(view.mwrPeriod.value).toBeCloseTo(0.13);
    expect(view.mwrAnnualized.value).toBeCloseTo(0.12);
  });
});
