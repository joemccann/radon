import { describe, expect, it } from "vitest";
import {
  groupCatalystsByCategory,
  catalystKindLabel,
  catalystPrintLabel,
  catalystWhenLabel,
} from "../lib/catalystGroups";
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

describe("groupCatalystsByCategory", () => {
  it("orders categories economic, earnings, fda, then the remainder", () => {
    const rows = [
      row({ type: "fda", ticker: "ABOS", title: "PDUFA date", date: "2026-08-06" }),
      row({ type: "earnings", ticker: "TSLA", title: "TSLA earnings", date: "2026-08-05" }),
      row({ type: "economic", title: "Core CPI", date: "2026-08-07" }),
      row({ type: "split" as CatalystRow["type"], ticker: "NVDA", title: "NVDA split", date: "2026-08-05" }),
    ];
    const groups = groupCatalystsByCategory(rows, new Set(), NOW);
    expect(groups.map((g) => g.key)).toEqual(["economic", "earnings", "fda", "split"]);
    expect(groups.map((g) => g.label)).toEqual(["ECONOMIC DATA", "EARNINGS", "FDA", "SPLIT"]);
  });

  it("omits categories with no upcoming rows", () => {
    const groups = groupCatalystsByCategory([row({ title: "Core CPI", date: "2026-08-07" })], new Set(), NOW);
    expect(groups.map((g) => g.key)).toEqual(["economic"]);
  });

  it("drops past events and non-held rows beyond the 5-session window", () => {
    const rows = [
      row({ title: "Old print", date: "2026-08-03" }),
      row({ title: "Distant print", date: "2026-08-20" }),
    ];
    expect(groupCatalystsByCategory(rows, new Set(), NOW)).toEqual([]);
  });

  it("keeps held-ticker rows at any distance and sorts them to the top of their category", () => {
    const rows = [
      row({ type: "earnings", ticker: "TSLA", title: "TSLA earnings", date: "2026-08-05" }),
      row({ type: "earnings", ticker: "AAPL", title: "AAPL far earnings", date: "2026-08-24" }),
    ];
    const [earnings] = groupCatalystsByCategory(rows, new Set(["AAPL"]), NOW);
    expect(earnings.rows.map((r) => r.title)).toEqual(["AAPL far earnings", "TSLA earnings"]);
    expect(earnings.rows.map((r) => r.isHeld)).toEqual([true, false]);
    expect(earnings.heldCount).toBe(1);
  });

  it("sorts non-held rows nearest-first inside a category", () => {
    const rows = [
      row({ title: "Core CPI", date: "2026-08-07" }),
      row({ title: "NFIB optimism index", date: "2026-08-04" }),
    ];
    const [economic] = groupCatalystsByCategory(rows, new Set(), NOW);
    expect(economic.rows.map((r) => r.title)).toEqual(["NFIB optimism index", "Core CPI"]);
  });

  it("recomputes days_until at read time", () => {
    // Stored days_until is a fossil; the group must use the recomputed value.
    const rows = [row({ title: "CPI", date: "2026-08-05", days_until: 99 })];
    const [economic] = groupCatalystsByCategory(rows, new Set(), NOW);
    expect(economic.rows[0]?.days_until).toBe(1);
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
  it("renders calendar date plus ET time when event_time parses", () => {
    expect(
      catalystWhenLabel(row({ event_time: "2026-08-04T14:00:00Z", days_until: 0 })),
    ).toBe("4 Aug 10:00 ET");
  });

  it("renders calendar date only when event_time is missing", () => {
    expect(catalystWhenLabel(row({ date: "2026-08-04", days_until: 0 }))).toBe("4 Aug");
    expect(catalystWhenLabel(row({ date: "2026-08-07", days_until: 3 }))).toBe("7 Aug");
  });
});

describe("catalystPrintLabel", () => {
  it("compacts large economic prints as A/F after actual", () => {
    expect(
      catalystPrintLabel(row({ type: "economic", actual: 221000, forecast: "221000" })),
    ).toBe("A 221k  F 221k");
  });

  it("uses F/P when economic actual is missing", () => {
    expect(
      catalystPrintLabel(row({ type: "economic", forecast: "221000", prev: "215000" })),
    ).toBe("F 221k  P 215k");
  });

  it("keeps percents and small decimals", () => {
    expect(
      catalystPrintLabel(row({ type: "economic", forecast: "1.1%", prev: "0.3%" })),
    ).toBe("F 1.1%  P 0.3%");
    expect(
      catalystPrintLabel(
        row({
          type: "earnings",
          ticker: "AAPL",
          title: "AAPL earnings",
          street_mean_est: 1.52,
          actual_eps: 1.61,
        }),
      ),
    ).toBe("A 1.61  F 1.52");
  });

  it("uses F only for unreported earnings", () => {
    expect(
      catalystPrintLabel(
        row({ type: "earnings", ticker: "AAPL", title: "AAPL earnings", street_mean_est: 1.52 }),
      ),
    ).toBe("F 1.52");
  });

  it("omits missing parts and passes already-short strings", () => {
    expect(catalystPrintLabel(row({ type: "economic", actual: "0.3%" }))).toBe("A 0.3%");
    expect(
      catalystPrintLabel(row({ type: "economic", forecast: "220K", prev: "218K" })),
    ).toBe("F 220K  P 218K");
    expect(catalystPrintLabel(row({ type: "fda", ticker: "ABOS", title: "PDUFA" }))).toBe("");
  });
});
