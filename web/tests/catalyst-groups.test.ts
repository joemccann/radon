import { describe, expect, it } from "vitest";
import { groupCatalysts, catalystKindLabel, catalystWhenLabel } from "../lib/catalystGroups";
import type { CatalystRow } from "../lib/useCatalysts";

// Tuesday 2026-08-04 10:00 ET — a regular trading day, well before the
// extended-session cutoff, so recomputed days_until match the row offsets.
const NOW = new Date("2026-08-04T14:00:00Z");

function row(overrides: Partial<CatalystRow>): CatalystRow {
  return {
    ticker: null,
    type: "economic",
    title: "Existing home sales",
    date: "2026-08-04",
    source: "economic",
    days_until: 0,
    ...overrides,
  };
}

describe("groupCatalysts", () => {
  it("splits rows into today / this week / positions", () => {
    const rows = [
      row({ title: "NFIB optimism index", date: "2026-08-04" }),
      row({ title: "Core CPI", date: "2026-08-07" }),
      row({ ticker: "MCHP", type: "earnings", title: "MCHP earnings", date: "2026-08-04" }),
    ];
    const groups = groupCatalysts(rows, new Set(["MCHP"]), NOW);
    expect(groups.today.map((r) => r.title)).toEqual(["NFIB optimism index"]);
    expect(groups.thisWeek.map((r) => r.title)).toEqual(["Core CPI"]);
    expect(groups.positions.map((r) => r.title)).toEqual(["MCHP earnings"]);
  });

  it("drops past events and rows beyond the 5-session window", () => {
    const rows = [
      row({ title: "Old print", date: "2026-08-03" }),
      row({ title: "Distant print", date: "2026-08-20" }),
      row({ title: "Edge of window", date: "2026-08-10" }), // +6 days but Monday = 4 sessions? days_until=6 > 5 → dropped
    ];
    const groups = groupCatalysts(rows, new Set(), NOW);
    expect(groups.today).toHaveLength(0);
    expect(groups.thisWeek).toHaveLength(0);
    expect(groups.positions).toHaveLength(0);
  });

  it("keeps position-linked rows out of today/thisWeek and in positions regardless of distance", () => {
    const rows = [
      row({ ticker: "AAPL", type: "earnings", title: "AAPL earnings", date: "2026-08-06" }),
      row({ ticker: "AAPL", type: "earnings", title: "AAPL far earnings", date: "2026-08-24" }),
    ];
    const groups = groupCatalysts(rows, new Set(["AAPL"]), NOW);
    expect(groups.thisWeek).toHaveLength(0);
    expect(groups.positions.map((r) => r.title)).toEqual(["AAPL earnings", "AAPL far earnings"]);
  });

  it("treats tickers not held as ordinary rows", () => {
    const rows = [row({ ticker: "TSLA", type: "earnings", title: "TSLA earnings", date: "2026-08-05" })];
    const groups = groupCatalysts(rows, new Set(["AAPL"]), NOW);
    expect(groups.positions).toHaveLength(0);
    expect(groups.thisWeek.map((r) => r.title)).toEqual(["TSLA earnings"]);
  });

  it("recomputes days_until at read time", () => {
    // Stored days_until is a fossil; the group must use the recomputed value.
    const rows = [row({ title: "CPI", date: "2026-08-05", days_until: 99 })];
    const groups = groupCatalysts(rows, new Set(), NOW);
    expect(groups.thisWeek[0]?.days_until).toBe(1);
  });
});

describe("catalystKindLabel", () => {
  it("maps types to compact kind labels", () => {
    expect(catalystKindLabel("economic")).toBe("ECON");
    expect(catalystKindLabel("earnings")).toBe("ER");
    expect(catalystKindLabel("fda")).toBe("FDA");
  });
});

describe("catalystWhenLabel", () => {
  it("prefers an exact event time rendered in ET", () => {
    expect(
      catalystWhenLabel(row({ event_time: "2026-08-04T14:00:00Z", days_until: 0 })),
    ).toBe("10:00 ET");
  });

  it("falls back to the days-until badge label", () => {
    expect(catalystWhenLabel(row({ days_until: 0 }))).toBe("Today");
    expect(catalystWhenLabel(row({ days_until: 4 }))).toBe("4d");
  });
});
