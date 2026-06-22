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

describe("assessMargin — cashToClear deposit suggestion", () => {
  it("IBKR-rule binding: screenshot scenario suggests ~$32,728 to clear", () => {
    // equity 1,338,997 within 10% of maint 1,247,023 (maint×1.10 = 1,371,725.30).
    // Healthy cushion + positive excess so the IBKR 110% rule is the binding constraint.
    const r = assessMargin(
      acct({
        equity_with_loan: 1_338_997,
        maintenance_margin: 1_247_023,
        net_liquidation: 1_500_000,
        excess_liquidity: 200_000, // 13.3% cushion
      }),
    );
    expect(r.level).toBe("warning");
    expect(r.message).toMatch(/within 10% of maintenance margin/i);
    // maint×1.10 − equity = 1,371,725.30 − 1,338,997 = 32,728.30; round UP to strictly clear.
    expect(r.cashToClear).toBe(32_729);
    expect(r.cashToClear!).toBeGreaterThanOrEqual(32_728.3);
    expect(r.message).toMatch(/Deposit ~\$32,729 in cash to clear this warning/);
  });

  it("a deposit of cashToClear actually clears the warning to none (IBKR-rule case)", () => {
    const input = {
      equity_with_loan: 1_338_997,
      maintenance_margin: 1_247_023,
      net_liquidation: 1_500_000,
      excess_liquidity: 200_000,
    };
    const D = assessMargin(acct(input)).cashToClear!;
    const after = assessMargin(
      acct({
        ...input,
        equity_with_loan: input.equity_with_loan + D,
        net_liquidation: input.net_liquidation + D,
        excess_liquidity: input.excess_liquidity + D,
      }),
    );
    expect(after.level).toBe("none");
    expect(after.cashToClear).toBeNull();
  });

  it("cushion binding: 4% cushion case picks the cushion constraint, not the IBKR rule", () => {
    // cushion = 40,000 / 1,000,000 = 4% (warning). EWL well above MMR×1.10, so EWL rule slack.
    const r = assessMargin(
      acct({
        excess_liquidity: 40_000,
        net_liquidation: 1_000_000,
        maintenance_margin: 100_000,
        equity_with_loan: 900_000,
      }),
    );
    expect(r.level).toBe("warning");
    // cushion: (0.05×1,000,000 − 40,000)/0.95 = 10,000/0.95 = 10,526.32 → ceil 10,527
    // excess: −40,000 (slack). EWL: 110,000 − 900,000 = −790,000 (slack). Max = cushion.
    expect(r.cashToClear).toBe(10_527);
    const D = r.cashToClear!;
    const after = assessMargin(
      acct({
        excess_liquidity: 40_000 + D,
        net_liquidation: 1_000_000 + D,
        maintenance_margin: 100_000,
        equity_with_loan: 900_000 + D,
      }),
    );
    expect(after.level).toBe("none");
  });

  it("none: healthy account has cashToClear null", () => {
    const r = assessMargin(acct({ excess_liquidity: 200_000, net_liquidation: 1_000_000, equity_with_loan: 500_000 }));
    expect(r.level).toBe("none");
    expect(r.cashToClear).toBeNull();
  });

  it("none: missing inputs has cashToClear null", () => {
    expect(assessMargin(null).cashToClear).toBeNull();
    expect(assessMargin(acct({ net_liquidation: 0 })).cashToClear).toBeNull();
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
