/**
 * REL-068 tranche B — R-163, R-164, R-166.
 *
 * Three places a derived number renders as current, precise or correctly
 * named when the input does not support it: a stale performance payload that
 * blanks only its headline, a trading-day gate that falls open once the
 * static holiday table runs out, and an executed-combo classifier that
 * throws away leg quantities.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const REPO = join(__dirname, "..", "..");

// ---------------------------------------------------------------------------
// R-163 — staleness must gate every derived statistic, not just the headline
// ---------------------------------------------------------------------------
async function viewFor(overrides: Record<string, unknown>) {
  const { buildPerformanceView } = await import("../lib/performanceData");
  const base = {
    schema_version: 2,
    status: "ok",
    nav_as_of: "2026-05-01",
    as_of: "2026-05-01",
    period_start: "2024-01-02",
    period_end: "2026-05-01",
    nav_sessions_behind: 80,
    twr: { cum_return: 0.42, annualized: { value: 0.18, n: 400, min_n: 60, unavailable_reason: null } },
    mwr: {
      period: { value: 0.4, n: 400, min_n: 60, unavailable_reason: null },
      annualized: { value: 0.17, n: 400, min_n: 60, unavailable_reason: null },
    },
    risk: {
      volatility: { value: 0.2, n: 400, min_n: 60, unavailable_reason: null },
      sharpe_ratio: { value: 1.2, n: 400, min_n: 60, unavailable_reason: null },
      sortino_ratio: { value: 1.6, n: 400, min_n: 60, unavailable_reason: null },
    },
    equity: { net_external_flows: 0, investment_pnl: 1000 },
    series: Array.from({ length: 400 }, (_, i) => ({
      date: `2024-${String(1 + (i % 12)).padStart(2, "0")}-01`,
      nav: 100000 + i,
    })),
    ...overrides,
  };
  const view = buildPerformanceView(base);
  if (!view) throw new Error("fixture did not build a view");
  return view;
}

describe("R-163: a stale NAV suppresses the derived statistics too", () => {
  it("blanks annualized, MWR and risk alongside the headline", async () => {
    const view = await viewFor({});
    expect(view.twrCumReturn).toBeNull();
    expect(view.annualized.value).toBeNull();
    expect(view.mwrPeriod.value).toBeNull();
    expect(view.mwrAnnualized.value).toBeNull();
    expect(view.risk.volatility.value).toBeNull();
    expect(view.risk.sharpe_ratio.value).toBeNull();
    expect(view.risk.sortino_ratio.value).toBeNull();
  });

  it("names staleness as the reason, not 'not computed'", async () => {
    const view = await viewFor({});
    expect(view.annualized.unavailable_reason).toBe("stale_nav");
  });

  it("renders copy that says the data is stale", async () => {
    const { gateCopy } = await import("../lib/performanceTwr");
    const copy = gateCopy(
      { value: null, n: 400, min_n: 60, unavailable_reason: "stale_nav" },
      "Annualized",
    );
    expect(copy).toMatch(/stale|behind/i);
  });

  it("a fresh payload still renders every statistic", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const view = await viewFor({ nav_as_of: today, as_of: today, nav_sessions_behind: 0 });
    expect(view.twrCumReturn).not.toBeNull();
    expect(view.annualized.value).not.toBeNull();
    expect(view.risk.sharpe_ratio.value).not.toBeNull();
  });

  it("a degraded payload keeps its own more specific reason", async () => {
    const today = new Date().toISOString().slice(0, 10);
    const view = await viewFor({
      status: "degraded",
      nav_as_of: today,
      as_of: today,
      nav_sessions_behind: 0,
    });
    expect(view.annualized.unavailable_reason).not.toBe("stale_nav");
  });
});

// ---------------------------------------------------------------------------
// R-164 — the static holiday table must not run out silently
// ---------------------------------------------------------------------------
describe("R-164: the holiday table's coverage is explicit and enforced", () => {
  it("exports the years it actually covers", async () => {
    const { HOLIDAY_TABLE_YEARS } = await import("../lib/serviceHealthWindows");
    expect(Array.isArray(HOLIDAY_TABLE_YEARS)).toBe(true);
    expect(HOLIDAY_TABLE_YEARS.length).toBeGreaterThan(0);
  });

  it("reports whether a given date is inside that coverage", async () => {
    const { isHolidayTableCovering, HOLIDAY_TABLE_YEARS } = await import(
      "../lib/serviceHealthWindows"
    );
    const covered = HOLIDAY_TABLE_YEARS[0];
    expect(isHolidayTableCovering(`${covered}-07-04`)).toBe(true);
    expect(isHolidayTableCovering("2099-07-04")).toBe(false);
  });

  it("covers this year and the next, so the gap is a CI failure not a silent 2028", () => {
    const raw = readFileSync(
      join(REPO, "scripts", "config", "market_holidays.json"),
      "utf-8",
    );
    const years = Object.keys(JSON.parse(raw) as Record<string, string[]>);
    const thisYear = new Date().getUTCFullYear();
    expect(years).toContain(String(thisYear));
    expect(years).toContain(String(thisYear + 1));
  });

  it("still classifies a covered holiday as a non-trading day", async () => {
    const { isUsTradingDay } = await import("../lib/serviceHealthWindows");
    expect(isUsTradingDay("2026-07-03")).toBe(false); // observed Independence Day
    expect(isUsTradingDay("2026-08-21")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// R-166 — an executed ratio spread is not a vertical
// ---------------------------------------------------------------------------
describe("R-166: executed combo labels carry the leg quantities", () => {
  const leg = (
    strike: number,
    side: string,
    quantity: number,
    right: "C" | "P" = "C",
  ) => ({
    execId: `e-${strike}-${side}-${quantity}`,
    symbol: "SPY",
    contract: { symbol: "SPY", secType: "OPT", strike, right, expiry: "2026-09-18" },
    side,
    quantity,
    avgPrice: 1,
    commission: 0,
    realizedPNL: null,
    time: "2026-08-21T14:00:00Z",
    exchange: "SMART",
  });

  it("names a 1x2 ratio spread as a ratio, not a vertical", async () => {
    const { buildExecutedGroupDescription } = await import("../lib/openOrderCombos");
    const label = buildExecutedGroupDescription(
      [leg(500, "BOT", 1), leg(520, "SLD", 2)] as never,
      false,
    );
    expect(label).not.toMatch(/Vertical|Call Spread/i);
    expect(label).toMatch(/Ratio/i);
  });

  it("still names a true 1x1 vertical a vertical", async () => {
    const { buildExecutedGroupDescription } = await import("../lib/openOrderCombos");
    const label = buildExecutedGroupDescription(
      [leg(500, "BOT", 1), leg(520, "SLD", 1)] as never,
      false,
    );
    expect(label).toMatch(/Spread|Vertical/i);
    expect(label).not.toMatch(/Ratio/i);
  });

  it("does not hardcode a unit quantity", () => {
    const src = readFileSync(join(REPO, "web", "lib", "openOrderCombos.ts"), "utf-8");
    const fn = src
      .split("function detectExecutedGroupStructure(")[1]
      .split("\n}")[0]
      .split("\n")
      .filter((line) => !line.trim().startsWith("//"))
      .join("\n");
    expect(fn).not.toMatch(/quantity:\s*1\b/);
  });
});
