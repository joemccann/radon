/**
 * @vitest-environment jsdom
 *
 * Short crowding / squeeze scoreboard (Equibles).
 *
 * Pure helpers (components/equibles/shortCrowding.ts): percent-safe formatting
 * (values reach the UI already normalized to 0-100 by EquiblesClient, never
 * fractions), the days-to-cover tier tone, the settlement VINTAGE label, and
 * the scoreboard summary.
 *
 * EquiblesShortCrowdingPanel: loading / empty gating, the summary strip, the
 * honest vintage line, both gate readings per row (Gate 1 upside convexity AND
 * Gate 3 short-leg tail risk), and the unresolved IB borrow seam.
 *
 * Settlement dates are window-relative (today minus N) so nothing rots in CI.
 */
import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildScoreboardSummary,
  formatChangePct,
  formatDaysToCover,
  formatShares,
  formatSqueezeScore,
  tierColor,
  vintageLabel,
  type ShortCrowdingData,
  type ShortCrowdingEntry,
} from "@/components/equibles/shortCrowding";

/* ─── Fixtures ───────────────────────────────────────── */

function daysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

const SETTLEMENT = daysAgo(9);

function entry(overrides: Partial<ShortCrowdingEntry> = {}): ShortCrowdingEntry {
  return {
    ticker: "GME",
    settlement_date: SETTLEMENT,
    settlement_age_days: 9,
    prior_settlement_date: daysAgo(24),
    short_position: 55_000_000,
    prior_short_position: 50_000_000,
    change_in_short_position: 5_000_000,
    short_position_change_pct: 10,
    average_daily_volume: 8_600_000,
    days_to_cover: 6.4,
    crowding_tier: "extreme",
    squeeze_score: 88,
    squeeze_rank: 3,
    base_score: 80,
    catalyst_boost: 8,
    short_interest_pct: 22.5,
    short_interest_pct_basis: "shares",
    market_cap: 12_400_000_000,
    factors: null,
    readings: { upside_convexity: "extreme", short_leg_tail_risk: "extreme" },
    borrow: { source: "ib-short-availability", resolved: false },
    ...overrides,
  };
}

function payload(overrides: Partial<ShortCrowdingData> = {}): ShortCrowdingData {
  return {
    scan_time: new Date().toISOString(),
    count: 2,
    latest_settlement_date: SETTLEMENT,
    entries: [
      entry(),
      entry({
        ticker: "AAPL",
        squeeze_score: 24,
        squeeze_rank: 812,
        days_to_cover: 1.1,
        crowding_tier: "light",
        short_position: 120_000_000,
        short_position_change_pct: -4.2,
        short_interest_pct: 0.8,
        readings: { upside_convexity: "low", short_leg_tail_risk: "low" },
      }),
    ],
    board: [],
    ...overrides,
  };
}

/* ─── Pure helpers ───────────────────────────────────── */

describe("formatSqueezeScore", () => {
  it("renders the 0-100 score with one decimal", () => {
    expect(formatSqueezeScore(88)).toBe("88.0");
    expect(formatSqueezeScore(7.25)).toBe("7.3");
  });

  it("returns --- for null and non-finite values", () => {
    expect(formatSqueezeScore(null)).toBe("---");
    expect(formatSqueezeScore(Number.NaN)).toBe("---");
  });
});

describe("formatDaysToCover", () => {
  it("renders sessions of average volume", () => {
    expect(formatDaysToCover(6.4)).toBe("6.4d");
    expect(formatDaysToCover(0)).toBe("0.0d");
  });

  it("returns --- when unknown", () => {
    expect(formatDaysToCover(null)).toBe("---");
  });
});

describe("formatShares", () => {
  it("scales to K/M/B", () => {
    expect(formatShares(55_000_000)).toBe("55.0M");
    expect(formatShares(1_240_000_000)).toBe("1.24B");
    expect(formatShares(4_300)).toBe("4.3K");
    expect(formatShares(420)).toBe("420");
  });

  it("returns --- when unknown", () => {
    expect(formatShares(null)).toBe("---");
  });
});

describe("formatChangePct", () => {
  it("signs the settlement-over-settlement change", () => {
    expect(formatChangePct(10)).toBe("+10.0%");
    expect(formatChangePct(-4.23)).toBe("-4.2%");
  });

  it("treats the value as a percent, never a fraction", () => {
    // EquiblesClient already converted 0.0423 -> 4.23 upstream.
    expect(formatChangePct(4.23)).toBe("+4.2%");
  });

  it("returns --- when unknown", () => {
    expect(formatChangePct(null)).toBe("---");
  });
});

describe("tierColor", () => {
  it("maps the crowding ladder to brand tokens, never raw hex", () => {
    for (const tier of ["extreme", "crowded", "moderate", "light", "unknown"]) {
      expect(tierColor(tier)).toMatch(/^var\(--|^color-mix\(/);
    }
  });

  it("escalates extreme above light", () => {
    expect(tierColor("extreme")).not.toBe(tierColor("light"));
  });
});

describe("vintageLabel", () => {
  it("states the settlement the figures belong to and its age", () => {
    expect(vintageLabel(SETTLEMENT, 9)).toBe(`SETTLED ${SETTLEMENT} · 9D AGO`);
  });

  it("derives the age when the payload omits it", () => {
    expect(vintageLabel(daysAgo(3), null)).toBe(`SETTLED ${daysAgo(3)} · 3D AGO`);
  });

  it("degrades without a settlement date", () => {
    expect(vintageLabel(null, null)).toBe("SETTLEMENT UNKNOWN");
  });
});

describe("buildScoreboardSummary", () => {
  it("picks the most crowded and highest-scoring names", () => {
    const summary = buildScoreboardSummary(payload().entries);
    expect(summary.mostCrowded?.ticker).toBe("GME");
    expect(summary.topScore?.ticker).toBe("GME");
    expect(summary.extremeCount).toBe(1);
  });

  it("tolerates entries missing scores and cover", () => {
    const summary = buildScoreboardSummary([
      entry({ ticker: "X", squeeze_score: null, days_to_cover: null, crowding_tier: "unknown" }),
    ]);
    expect(summary.topScore).toBeNull();
    expect(summary.mostCrowded).toBeNull();
    expect(summary.extremeCount).toBe(0);
  });

  it("returns an empty summary for no entries", () => {
    expect(buildScoreboardSummary([])).toEqual({
      mostCrowded: null,
      topScore: null,
      extremeCount: 0,
    });
  });
});

/* ─── Panel ──────────────────────────────────────────── */

const mockUseShortCrowding = vi.fn();
vi.mock("@/components/equibles/useEquiblesShortCrowding", () => ({
  useEquiblesShortCrowding: () => mockUseShortCrowding(),
}));

async function renderPanel(state: Record<string, unknown>) {
  mockUseShortCrowding.mockReturnValue({
    data: null,
    loading: false,
    syncing: false,
    error: null,
    lastSync: null,
    syncNow: () => {},
    ...state,
  });
  const { default: Panel } = await import("@/components/equibles/EquiblesShortCrowdingPanel");
  return render(<Panel />);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("EquiblesShortCrowdingPanel", () => {
  it("shows the loader before the first payload", async () => {
    await renderPanel({ loading: true });
    expect(screen.getByText(/short crowding/i)).toBeTruthy();
    expect(screen.queryByTestId("short-crowding-table")).toBeNull();
  });

  it("shows the empty state when the payload is missing", async () => {
    await renderPanel({ data: { missing: true, count: 0, entries: [], board: [] } });
    expect(screen.getByText(/no short interest data yet/i)).toBeTruthy();
  });

  it("renders one row per ticker, ranked as delivered", async () => {
    await renderPanel({ data: payload() });
    const rows = screen.getAllByTestId(/^short-crowding-row-/);
    expect(rows.map((r) => r.getAttribute("data-ticker"))).toEqual(["GME", "AAPL"]);
  });

  it("renders BOTH gate readings on every row", async () => {
    await renderPanel({ data: payload() });
    const row = within(screen.getByTestId("short-crowding-row-GME"));
    expect(row.getByTestId("short-crowding-upside-GME").textContent).toMatch(/EXTREME/i);
    expect(row.getByTestId("short-crowding-tail-GME").textContent).toMatch(/EXTREME/i);
  });

  it("states the settlement vintage instead of implying live data", async () => {
    await renderPanel({ data: payload() });
    const vintage = screen.getByTestId("short-crowding-vintage").textContent ?? "";
    expect(vintage).toContain(SETTLEMENT);
    expect(vintage).toMatch(/9D AGO/);
    expect(vintage).not.toMatch(/live|real-?time/i);
  });

  it("summarises the board in the strip", async () => {
    await renderPanel({ data: payload() });
    expect(screen.getByTestId("short-crowding-strip-crowded").textContent).toContain("GME");
    expect(screen.getByTestId("short-crowding-strip-score").textContent).toContain("88.0");
    expect(screen.getByTestId("short-crowding-strip-covered").textContent).toContain("2");
  });

  it("declares the squeeze score is not a directional signal", async () => {
    await renderPanel({ data: payload() });
    expect(screen.getByTestId("short-crowding-footnote").textContent).toMatch(
      /not a directional/i,
    );
  });

  it("marks the IB borrow join as unresolved rather than faking a number", async () => {
    await renderPanel({ data: payload() });
    expect(screen.getByTestId("short-crowding-borrow-GME").textContent).toBe("---");
  });

  it("renders --- for a ticker with no squeeze score", async () => {
    await renderPanel({
      data: payload({
        entries: [entry({ ticker: "ZZZ", squeeze_score: null, squeeze_rank: null })],
        count: 1,
      }),
    });
    const row = within(screen.getByTestId("short-crowding-row-ZZZ"));
    expect(row.getByTestId("short-crowding-score-ZZZ").textContent).toBe("---");
  });
});
