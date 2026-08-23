import { describe, expect, it } from "vitest";
import { deriveKpis, fmtMoneyExact } from "../lib/dashboardKpis";
import type { PortfolioData } from "../lib/types";

function portfolioWith(account: Partial<PortfolioData["account_summary"]> | undefined): PortfolioData {
  return {
    bankroll: 0,
    peak_value: 0,
    last_sync: "2026-08-07T16:35:18-04:00",
    positions: [],
    total_deployed_pct: 0,
    total_deployed_dollars: 0,
    remaining_capacity_pct: 0,
    position_count: 0,
    defined_risk_count: 0,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
    account_summary: account
      ? ({
          net_liquidation: 2_847_120,
          daily_pnl: 18_432,
          unrealized_pnl: 0,
          realized_pnl: 0,
          settled_cash: 0,
          maintenance_margin: 894_000,
          excess_liquidity: 1_100_000,
          buying_power: 1_204_580,
          dividends: 0,
          ...account,
        } as PortfolioData["account_summary"])
      : undefined,
  };
}

describe("deriveKpis", () => {
  it("returns four cells with null values when portfolio is absent", () => {
    const cells = deriveKpis(null, 0);
    expect(cells).toHaveLength(4);
    expect(cells.map((c) => c.key)).toEqual(["netLiq", "todayPnl", "buyingPower", "marginUsed"]);
    for (const cell of cells) {
      expect(cell.value).toBeNull();
      expect(cell.display).toBe("—");
      expect(cell.barPct).toBeNull();
    }
  });

  it("maps net liquidation with exact digits", () => {
    const [netLiq] = deriveKpis(portfolioWith({}), 0);
    expect(netLiq.value).toBe(2_847_120);
    expect(netLiq.display).toBe("$2,847,120");
  });

  it("prefers IB daily_pnl for today P&L and tones positive as core", () => {
    const [, todayPnl] = deriveKpis(portfolioWith({ daily_pnl: 18_432 }), 999, new Date("2026-08-07T18:30:00Z")); // Fri 14:30 ET
    expect(todayPnl.value).toBe(18_432);
    expect(todayPnl.tone).toBe("core");
  });

  it("falls back to realized fills when daily_pnl is null", () => {
    const [, todayPnl] = deriveKpis(portfolioWith({ daily_pnl: null }), -1_250);
    expect(todayPnl.value).toBe(-1_250);
    expect(todayPnl.tone).toBe("fault");
  });

  it("leaves today P&L null when daily_pnl is null and no fills", () => {
    const [, todayPnl] = deriveKpis(portfolioWith({ daily_pnl: null }), 0);
    expect(todayPnl.value).toBeNull();
    expect(todayPnl.tone).toBe("neutral");
  });

  it("derives buying-power bar from available_funds / equity_with_loan", () => {
    const [, , bp] = deriveKpis(
      portfolioWith({ available_funds: 900_000, equity_with_loan: 3_000_000 }),
      0,
    );
    expect(bp.value).toBe(1_204_580);
    expect(bp.barPct).toBeCloseTo(30, 5);
  });

  it("omits the buying-power bar when capacity inputs are missing", () => {
    const [, , bp] = deriveKpis(portfolioWith({}), 0);
    expect(bp.barPct).toBeNull();
  });

  it("derives margin used % from maintenance_margin / net_liquidation", () => {
    const cells = deriveKpis(portfolioWith({}), 0);
    const margin = cells[3];
    expect(margin.value).toBeCloseTo((894_000 / 2_847_120) * 100, 5);
    expect(margin.display).toBe(`${((894_000 / 2_847_120) * 100).toFixed(1)}%`);
    expect(margin.barPct).toBeCloseTo((894_000 / 2_847_120) * 100, 5);
  });

  it("tones margin warn when assessMargin is none, fault when warning", () => {
    const healthy = deriveKpis(portfolioWith({}), 0)[3];
    expect(healthy.barTone).toBe("warn");

    const strained = deriveKpis(
      portfolioWith({ excess_liquidity: 50_000 }), // cushion < 5%
      0,
    )[3];
    expect(strained.barTone).toBe("fault");
  });

  it("keeps margin null when net_liquidation is non-positive", () => {
    const margin = deriveKpis(portfolioWith({ net_liquidation: 0 }), 0)[3];
    expect(margin.value).toBeNull();
    expect(margin.barPct).toBeNull();
  });
});

describe("fmtMoneyExact", () => {
  it("renders full digits without abbreviation", () => {
    expect(fmtMoneyExact(2_847_120)).toBe("$2,847,120");
  });
  it("renders placeholder for nullish", () => {
    expect(fmtMoneyExact(null)).toBe("—");
  });
  it("uses typographic minus for negatives", () => {
    expect(fmtMoneyExact(-1_250)).toBe("−$1,250");
  });
});

describe("deriveKpis Today P&L on a non-trading day", () => {
  it("blanks IB daily_pnl on Saturday instead of calling it today", () => {
    const saturday = new Date("2026-08-22T21:23:00Z");
    const cell = deriveKpis(portfolioWith({ daily_pnl: 13_951.76 }), 0, saturday)
      .find((c) => c.key === "todayPnl");
    expect(cell?.value).toBeNull();
    expect(cell?.display).toBe("—");
  });

  it("keeps IB daily_pnl on a trading day the snapshot was taken in", () => {
    // REL-048 / R-107: "a trading day" is no longer enough — the snapshot
    // has to have been CAPTURED during it. The fixture's own last_sync is
    // 2026-08-07, so the date is now part of this assertion.
    const friday = new Date("2026-08-21T18:30:00Z");
    const portfolio = {
      ...portfolioWith({ daily_pnl: -5_339.04 }),
      last_sync: "2026-08-21T18:29:00Z",
    };
    const cell = deriveKpis(portfolio, 0, friday).find((c) => c.key === "todayPnl");
    expect(cell?.value).toBeCloseTo(-5_339.04);
  });

  it("blanks a daily_pnl captured on an EARLIER trading day", () => {
    const friday = new Date("2026-08-21T18:30:00Z");
    const cell = deriveKpis(portfolioWith({ daily_pnl: -5_339.04 }), 0, friday)
      .find((c) => c.key === "todayPnl");
    expect(cell?.value).toBeNull();
  });
});
