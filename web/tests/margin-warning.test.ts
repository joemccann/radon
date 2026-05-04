import { describe, expect, it } from "vitest";
import { assessMargin, rankOf } from "../lib/marginWarning";
import type { AccountSummary } from "../lib/types";

function acct(overrides: Partial<AccountSummary> = {}): AccountSummary {
  return {
    net_liquidation: 1_000_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 0,
    maintenance_margin: 100_000,
    excess_liquidity: 100_000,
    buying_power: 0,
    dividends: 0,
    ...overrides,
  };
}

describe("assessMargin — threshold matrix", () => {
  it("none: 10% cushion is healthy", () => {
    const r = assessMargin(acct({ excess_liquidity: 100_000, net_liquidation: 1_000_000 }));
    expect(r.level).toBe("none");
    expect(r.cushionPct).toBeCloseTo(10, 5);
  });

  it("warning: 4% cushion (between 1% and 5%)", () => {
    const r = assessMargin(acct({ excess_liquidity: 40_000, net_liquidation: 1_000_000 }));
    expect(r.level).toBe("warning");
    expect(r.cushionPct).toBeCloseTo(4, 5);
    expect(r.message).toMatch(/Approaching margin call/i);
  });

  it("critical: 0.9% cushion (below 1%)", () => {
    const r = assessMargin(acct({ excess_liquidity: 9_000, net_liquidation: 1_000_000 }));
    expect(r.level).toBe("critical");
    expect(r.cushionPct).toBeCloseTo(0.9, 5);
    expect(r.message).toMatch(/imminent/i);
  });

  it("critical: Excess Liquidity exactly zero (active call)", () => {
    const r = assessMargin(acct({ excess_liquidity: 0 }));
    expect(r.level).toBe("critical");
    expect(r.cushionPct).toBe(0);
    expect(r.message).toMatch(/Margin call/i);
  });

  it("critical: Excess Liquidity negative (deeper margin call)", () => {
    const r = assessMargin(acct({ excess_liquidity: -5_000 }));
    expect(r.level).toBe("critical");
    expect(r.cushionPct).toBe(0);
    expect(r.message).toMatch(/−\$5,000/);
  });

  it("none: null account (no cry-wolf on missing data)", () => {
    expect(assessMargin(null).level).toBe("none");
    expect(assessMargin(undefined).level).toBe("none");
  });

  it("none: net_liquidation zero or negative (avoid div-by-zero)", () => {
    const r = assessMargin(acct({ net_liquidation: 0 }));
    expect(r.level).toBe("none");
    expect(r.cushionPct).toBeNull();
  });

  it("warning: IBKR 110% rule fires even with healthy cushion", () => {
    // 8% cushion (would be `none` by cushion rule) BUT EWL is within 10% of MMR.
    const r = assessMargin(
      acct({
        excess_liquidity: 80_000,
        net_liquidation: 1_000_000,
        maintenance_margin: 100_000,
        equity_with_loan: 105_000, // ≤ 100,000 × 1.10
      }),
    );
    expect(r.level).toBe("warning");
    expect(r.message).toMatch(/within 10% of maintenance margin/i);
  });

  it("none: healthy cushion AND EWL well above the 110% rule", () => {
    const r = assessMargin(
      acct({
        excess_liquidity: 200_000,
        net_liquidation: 1_000_000,
        maintenance_margin: 100_000,
        equity_with_loan: 500_000,
      }),
    );
    expect(r.level).toBe("none");
  });
});

describe("rankOf — used by transition logic in WorkspaceShell", () => {
  it("orders levels none < warning < critical", () => {
    expect(rankOf("none")).toBeLessThan(rankOf("warning"));
    expect(rankOf("warning")).toBeLessThan(rankOf("critical"));
  });
});

describe("assessMargin — key stability", () => {
  it("same level returns the same key (no false transitions)", () => {
    const a = assessMargin(acct({ excess_liquidity: 40_000 }));
    const b = assessMargin(acct({ excess_liquidity: 41_000 })); // still warning
    expect(a.key).toBe(b.key);
  });

  it("worsening from warning to critical changes key", () => {
    const a = assessMargin(acct({ excess_liquidity: 40_000 })); // warning
    const b = assessMargin(acct({ excess_liquidity: 5_000 })); // critical
    expect(a.key).not.toBe(b.key);
  });
});
