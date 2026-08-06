import { describe, it, expect, vi } from "vitest";
import {
  TWR_CURVE_TYPE,
  TWR_RETURN_BASIS,
  TWR_LIBRARY_STRATEGY,
  buildTwrMethodology,
  annualizedTwr,
  isBetaGated,
  isVarGated,
} from "../lib/performanceTwr";
import { isPerformanceBehindPortfolioSync } from "../lib/performanceFreshness";

// ---------------------------------------------------------------------------
// curve_type / return_basis / library_strategy
// ---------------------------------------------------------------------------

describe("TWR methodology constants", () => {
  it("curve_type is twr_modified_dietz_daily", () => {
    expect(TWR_CURVE_TYPE).toBe("twr_modified_dietz_daily");
    const m = buildTwrMethodology(0.0412);
    expect(m.curve_type).toBe("twr_modified_dietz_daily");
  });

  it("return_basis is time_weighted", () => {
    expect(TWR_RETURN_BASIS).toBe("time_weighted");
    const m = buildTwrMethodology(0.0412);
    expect(m.return_basis).toBe("time_weighted");
  });

  it("library_strategy is fred_dgs3mo", () => {
    expect(TWR_LIBRARY_STRATEGY).toBe("fred_dgs3mo");
    const m = buildTwrMethodology(0.0412);
    expect(m.library_strategy).toBe("fred_dgs3mo");
  });
});

// ---------------------------------------------------------------------------
// risk_free_rate > 0
// ---------------------------------------------------------------------------

describe("risk_free_rate", () => {
  it("is > 0 when FRED DGS3MO is available", () => {
    const m = buildTwrMethodology(0.0412);
    expect(m.risk_free_rate).toBeGreaterThan(0);
    expect(m.risk_free_rate).toBeCloseTo(0.0412, 4);
  });

  it("methodology carries the Rf that the builder resolved", () => {
    // Builder writes payload.methodology.risk_free_rate from FRED or fallback.
    // Here we assert the contract: Rf must be a finite number and, when
    // FRED is configured, >0.  Fallback 0 is allowed but must carry a warning
    // (tested in Python suite).
    const withRf = buildTwrMethodology(0.052);
    expect(Number.isFinite(withRf.risk_free_rate)).toBe(true);
    expect(withRf.risk_free_rate).toBeGreaterThan(0);

    const fallback = buildTwrMethodology(0.0);
    expect(fallback.risk_free_rate).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// N gates — annualized
// ---------------------------------------------------------------------------

describe("annualized N gate", () => {
  it("N<20 → annualized is null", () => {
    expect(annualizedTwr(0.05, 3)).toBeNull();
    expect(annualizedTwr(0.05, 19)).toBeNull();
    expect(annualizedTwr(0.10, 0)).toBeNull();
  });

  it("N>=20 → annualized is computed", () => {
    const val = annualizedTwr(0.05, 20);
    expect(val).not.toBeNull();
    expect(val).toBeCloseTo(Math.pow(1.05, 252 / 20) - 1, 8);

    const val40 = annualizedTwr(0.10, 40);
    expect(val40).not.toBeNull();
  });

  it("annualized uses TRADING_DAYS=252", () => {
    const tr = 0.123456;
    const n = 60;
    expect(annualizedTwr(tr, n)).toBeCloseTo(Math.pow(1 + tr, 252 / n) - 1, 10);
  });
});

// ---------------------------------------------------------------------------
// Beta / VaR N gates
// ---------------------------------------------------------------------------

describe("metric N gates", () => {
  it("beta gated when N<40", () => {
    expect(isBetaGated(3)).toBe(true);
    expect(isBetaGated(39)).toBe(true);
    expect(isBetaGated(40)).toBe(false);
  });

  it("VaR gated when N<60", () => {
    expect(isVarGated(40)).toBe(true);
    expect(isVarGated(59)).toBe(true);
    expect(isVarGated(60)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// isPerformanceBehindPortfolioSync with mocked portfolioLastSync
// ---------------------------------------------------------------------------

describe("isPerformanceBehindPortfolioSync", () => {
  it("returns false when performance matches portfolio last_sync", () => {
    const lastSync = "2026-05-09T16:00:00Z";
    const perf = { as_of: "2026-05-09", last_sync: lastSync };
    expect(isPerformanceBehindPortfolioSync(perf as any, lastSync)).toBe(false);
  });

  it("returns true when portfolio has advanced past performance", () => {
    const perf = { as_of: "2026-05-08", last_sync: "2026-05-08T16:00:00Z" };
    const portfolioLastSync = "2026-05-09T16:00:00Z";
    expect(isPerformanceBehindPortfolioSync(perf as any, portfolioLastSync)).toBe(true);
  });

  it("returns true when last_sync mismatches even if as_of matches", () => {
    // as_of matches but last_sync string differs (rerun same ET day with new timestamp)
    const perf = { as_of: "2026-05-09", last_sync: "2026-05-09T14:00:00Z" };
    const portfolioLastSync = "2026-05-09T16:00:00Z";
    expect(isPerformanceBehindPortfolioSync(perf as any, portfolioLastSync)).toBe(true);
  });

  it("returns false when portfolioLastSync is null", () => {
    const perf = { as_of: "2026-05-09", last_sync: "2026-05-09T16:00:00Z" };
    expect(isPerformanceBehindPortfolioSync(perf as any, null)).toBe(false);
    expect(isPerformanceBehindPortfolioSync(perf as any, undefined)).toBe(false);
  });

  it("returns false when performance is null", () => {
    expect(isPerformanceBehindPortfolioSync(null, "2026-05-09T16:00:00Z")).toBe(false);
    expect(isPerformanceBehindPortfolioSync(undefined, "2026-05-09T16:00:00Z")).toBe(false);
  });

  it("handles naive UTC timestamps via ET conversion (midnight drift guard)", () => {
    // Naive timestamp written as datetime.now().isoformat() on Hetzner (UTC)
    // 2026-05-09T01:00:00 naive -> treated as UTC -> ET 2026-05-08
    // So portfolio ET as_of differs from performance that already advanced to 05-09
    const naiveSync = "2026-05-09T01:00:00.123456";
    const perf = { as_of: "2026-05-09", last_sync: "2026-05-09T16:00:00Z" };
    // naiveSync ET date is 2026-05-08, perf is 05-09 => behind
    expect(isPerformanceBehindPortfolioSync(perf as any, naiveSync)).toBe(true);
  });

  it("mocked portfolioLastSync advances across calls", () => {
    const spy = vi.fn();
    const perf = { as_of: "2026-05-09", last_sync: "2026-05-09T10:00:00Z" };
    // Simulate WorkspaceShell polling: first call behind, second after rebuild not behind
    expect(isPerformanceBehindPortfolioSync(perf as any, "2026-05-09T10:00:00Z")).toBe(false);
    expect(isPerformanceBehindPortfolioSync(perf as any, "2026-05-10T10:00:00Z")).toBe(true);
    spy.mockReturnValueOnce(perf);
  });
});

// ---------------------------------------------------------------------------
// Payload shape contract
// ---------------------------------------------------------------------------

describe("TWR payload contract", () => {
  it("methodology curve_type is twr_modified_dietz_daily and Rf>0 in a complete payload", () => {
    const payload = {
      methodology: buildTwrMethodology(0.0412),
      summary: {
        total_return: 0.123456,
        annualized_return: annualizedTwr(0.123456, 60),
        trading_days: 60,
      },
    };
    expect(payload.methodology.curve_type).toBe("twr_modified_dietz_daily");
    expect(payload.methodology.risk_free_rate).toBeGreaterThan(0);
    expect(payload.summary.annualized_return).not.toBeNull();
  });

  it("payload with N<20 has annualized null", () => {
    const n = 3;
    const payload = {
      methodology: buildTwrMethodology(0.04),
      summary: {
        total_return: 0.02,
        annualized_return: annualizedTwr(0.02, n),
        trading_days: n,
      },
    };
    expect(payload.summary.annualized_return).toBeNull();
    expect(payload.summary.trading_days).toBeLessThan(20);
  });
});
