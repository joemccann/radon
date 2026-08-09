/** Open-position return must use verified risk capital, never opening credit. */
import { describe, expect, it } from "vitest";
import {
  describeReturnCapital,
  getPnlCapital,
  getPnlDollars,
  getPnlPct,
  resolveReturnCapital,
  resolveEntryCost,
  resolveMarketValue,
} from "../lib/positionUtils";
import type { PortfolioPosition } from "../lib/types";

const SPCX_CREDIT_RR: PortfolioPosition = {
  id: 1,
  ticker: "SPCX",
  structure: "Ratio Risk Reversal 60x30 (P$120.0/C$135.0)",
  structure_type: "Ratio Risk Reversal",
  risk_profile: "undefined",
  expiry: "2026-08-21",
  contracts: 60,
  direction: "COMBO",
  entry_cost: -1514,
  max_risk: null,
  market_value: null,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-07-01",
  legs: [
    {
      direction: "LONG",
      contracts: 60,
      type: "Call",
      strike: 135,
      entry_cost: 48429,
      avg_cost: 807.15,
      market_price: 7.0,
      market_value: 42000,
    },
    {
      direction: "SHORT",
      contracts: 30,
      type: "Put",
      strike: 120,
      entry_cost: 49943,
      avg_cost: 1664.77,
      market_price: 5.05,
      market_value: 15150,
    },
  ],
};

describe("getPnlPct - verified return capital", () => {
  it("dollar P&L is MV − signed entry (credit RR)", () => {
    const ec = resolveEntryCost(SPCX_CREDIT_RR);
    const mv = resolveMarketValue(SPCX_CREDIT_RR);
    expect(ec).toBeCloseTo(-1514, 0);
    // long 42000 − short 15150 = 26850
    expect(mv).toBeCloseTo(26850, 0);
    expect(getPnlDollars(SPCX_CREDIT_RR)).toBeCloseTo(28364, 0);
  });

  it("returns null for SPCX when no verified capital basis exists", () => {
    expect(resolveReturnCapital(SPCX_CREDIT_RR)).toBeNull();
    expect(getPnlCapital(SPCX_CREDIT_RR)).toBeNull();
    expect(getPnlPct(SPCX_CREDIT_RR)).toBeNull();
    expect(describeReturnCapital(null)).toContain("unavailable");
  });

  it("does not trust a bare projected what-if margin", () => {
    const pos: PortfolioPosition = {
      ...SPCX_CREDIT_RR,
      init_margin_at_entry: 40_000,
    };
    expect(resolveReturnCapital(pos)).toBeNull();
    expect(getPnlPct(pos)).toBeNull();
  });

  it("uses isolated broker-observed margin with complete v2 provenance", () => {
    const pos: PortfolioPosition = {
      ...SPCX_CREDIT_RR,
      position_instance_id: "PI-U1-SPCX-1",
      account_id: "U1",
      legs: [
        { ...SPCX_CREDIT_RR.legs[0], con_id: 101 },
        { ...SPCX_CREDIT_RR.legs[1], con_id: 102 },
      ],
      return_capital: {
        version: 2,
        amount: 40_000,
        currency: "USD",
        measurement: {
          quality: "observed",
          method: "isolated-account-margin-delta",
          measured_at: "2026-07-01T15:31:00Z",
          observation_id: "OBS-1",
          isolation: "isolated",
          before_sample_id: "SAMPLE-1",
          after_sample_id: "SAMPLE-2",
          window_seconds: 60,
          concurrent_exec_ids: [],
        },
        linkage: {
          state: "linked",
          account_id: "U1",
          position_instance_id: "PI-U1-SPCX-1",
          con_ids: [101, 102],
          order_refs: ["radon-spcx-1"],
          perm_ids: [9001],
          exec_ids: ["E1", "E2"],
          legs: [
            { con_id: 101, currency: "USD", multiplier: 100 },
            { con_id: 102, currency: "USD", multiplier: 100 },
          ],
        },
      },
    };
    expect(resolveReturnCapital(pos)).toEqual({
      amount: 40_000,
      kind: "opening-margin",
      source: "ib-account-margin-delta",
      asOf: "2026-07-01T15:31:00Z",
      quality: "observed",
    });
    expect(getPnlPct(pos)).toBeCloseTo(70.91, 1);
  });

  it("rejects projected, unlinked, and incomplete opening-margin metadata", () => {
    const projected: PortfolioPosition = {
      ...SPCX_CREDIT_RR,
      return_capital: {
        amount: 40_000,
        kind: "opening-margin",
        source: "ib-whatif",
        as_of: "2026-07-01T15:30:00Z",
        quality: "estimated",
        fill_linked: false,
      },
    };
    const missingSource: PortfolioPosition = {
      ...SPCX_CREDIT_RR,
      return_capital: {
        amount: 40_000,
        kind: "opening-margin",
        source: null,
        as_of: "2026-07-01T15:31:00Z",
        quality: "fill-linked",
        fill_linked: true,
      },
    };
    const mislabeledWhatIf: PortfolioPosition = {
      ...SPCX_CREDIT_RR,
      return_capital: {
        amount: 40_000,
        kind: "opening-margin",
        source: "ib-whatif",
        as_of: "2026-07-01T15:31:00Z",
        quality: "exact",
        fill_linked: true,
      },
    };
    expect(resolveReturnCapital(projected)).toBeNull();
    expect(resolveReturnCapital(missingSource)).toBeNull();
    expect(resolveReturnCapital(mislabeledWhatIf)).toBeNull();
  });

  it("rejects a linked v2 estimated margin observation", () => {
    const pos: PortfolioPosition = {
      ...SPCX_CREDIT_RR,
      account_id: "U1",
      position_instance_id: "PI-U1-SPCX-1",
      legs: [
        { ...SPCX_CREDIT_RR.legs[0], con_id: 101 },
        { ...SPCX_CREDIT_RR.legs[1], con_id: 102 },
      ],
      return_capital: {
        version: 2,
        amount: 40_000,
        currency: "USD",
        measurement: {
          quality: "estimated",
          method: "ib-whatif",
          measured_at: "2026-07-01T15:31:00Z",
        },
        linkage: {
          state: "linked",
          account_id: "U1",
          position_instance_id: "PI-U1-SPCX-1",
          con_ids: [101, 102],
          order_refs: ["radon-spcx-1"],
          perm_ids: [9001],
          exec_ids: ["E1", "E2"],
          legs: [
            { con_id: 101, currency: "USD", multiplier: 100 },
            { con_id: 102, currency: "USD", multiplier: 100 },
          ],
        },
      },
    };

    expect(resolveReturnCapital(pos)).toBeNull();
    expect(getPnlPct(pos)).toBeNull();
  });

  it("rejects legacy margin metadata even when labeled fill-linked", () => {
    const pos: PortfolioPosition = {
      ...SPCX_CREDIT_RR,
      init_margin_at_entry: 40_000,
      init_margin_at_entry_metadata: {
        kind: "opening-margin",
        source: "ib-fill-ledger",
        as_of: "2026-07-01T15:31:00Z",
        quality: "fill-linked",
        fill_linked: true,
      },
    };
    expect(getPnlCapital(pos)).toBeNull();
  });

  it("prefers exact max risk for defined-risk positions", () => {
    const pos: PortfolioPosition = {
      ...SPCX_CREDIT_RR,
      risk_profile: "defined",
      max_risk: 10_000,
    };
    expect(resolveReturnCapital(pos)).toEqual({
      amount: 10_000,
      kind: "max-risk",
      source: "position.max_risk",
      asOf: null,
      quality: "exact",
    });
    expect(getPnlPct(pos)).toBeCloseTo(283.64, 2);
  });

  it("uses debit paid for an all-long option whose debit is the full loss", () => {
    const longCall: PortfolioPosition = {
      id: 2,
      ticker: "GLD",
      structure: "Long Call $450",
      structure_type: "Long Call",
      risk_profile: "undefined",
      expiry: "2027-01-15",
      contracts: 10,
      direction: "LONG",
      entry_cost: 5_000,
      max_risk: null,
      market_value: 7_500,
      legs: [{
        direction: "LONG",
        contracts: 10,
        type: "Call",
        strike: 450,
        entry_cost: 5_000,
        avg_cost: 500,
        market_price: 7.5,
        market_value: 7_500,
      }],
      kelly_optimal: null,
      target: null,
      stop: null,
      entry_date: "2026-08-07",
    };
    expect(resolveReturnCapital(longCall)).toMatchObject({
      amount: 5_000,
      kind: "debit-paid",
      source: "position.entry_cost",
      quality: "exact",
    });
    expect(getPnlPct(longCall)).toBe(50);
  });

  it("uses invested capital for long stock but not for short stock", () => {
    const longStock: PortfolioPosition = {
      ...SPCX_CREDIT_RR,
      ticker: "GLD",
      structure: "Stock (100 shares)",
      structure_type: "Stock",
      risk_profile: "equity",
      expiry: "N/A",
      contracts: 100,
      direction: "LONG",
      entry_cost: 20_000,
      market_value: 22_000,
      legs: [{
        direction: "LONG",
        contracts: 100,
        type: "Stock",
        strike: null,
        entry_cost: 20_000,
        avg_cost: 200,
        market_price: 220,
        market_value: 22_000,
      }],
    };
    expect(resolveReturnCapital(longStock)).toMatchObject({
      amount: 20_000,
      kind: "debit-paid",
    });
    expect(getPnlPct(longStock)).toBe(10);

    const shortStock: PortfolioPosition = {
      ...longStock,
      direction: "SHORT",
      entry_cost: 20_000,
      market_value: -18_000,
      legs: [{ ...longStock.legs[0], direction: "SHORT", market_value: 18_000 }],
    };
    expect(resolveReturnCapital(shortStock)).toBeNull();
  });

  it("rejects estimated and versionless capital for a complex position", () => {
    const complex: PortfolioPosition = {
      ...SPCX_CREDIT_RR,
      risk_profile: "complex",
      return_capital: {
        amount: 60_000,
        kind: "opening-margin",
        source: "ib-fill-ledger",
        as_of: "2026-07-01T15:31:00Z",
        quality: "fill-linked",
        fill_linked: true,
      } as never,
    };
    expect(getPnlCapital(complex)).toBeNull();
    expect(getPnlPct(complex)).toBeNull();
  });

  it("rejects ib-reg-t-standalone even when mislabeled as fill-linked", () => {
    const pos: PortfolioPosition = {
      ...SPCX_CREDIT_RR,
      return_capital: {
        amount: 127_000,
        kind: "opening-margin",
        source: "ib-reg-t-standalone",
        as_of: "2026-07-01T16:00:00Z",
        quality: "fill-linked",
        fill_linked: true,
      } as never,
    };
    expect(resolveReturnCapital(pos)).toBeNull();
    expect(getPnlCapital(pos)).toBeNull();
    expect(getPnlPct(pos)).toBeNull();
  });
});
