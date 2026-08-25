/**
 * @vitest-environment jsdom
 *
 * VOL CONE scanner tab — cheap 10% OTM wing IV scanner.
 *
 * Pure helpers (lib/volCone.ts): IV/percentile formatting, hit + regime
 * tones, chart-row mapping. VolConePanel: loader / empty / strip / table /
 * chart title / ALL-HITS chips / NaN path guard.
 * Spec: docs/indicators/vol-cone.md.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  buildVolConeChartRows,
  formatIvPct,
  formatMonthlyExpiry,
  formatPercentile,
  isHit,
  volConeRegimeColor,
  type VolConeData,
  type VolConeName,
  type VolConeSeriesPoint,
} from "@/lib/volCone";

describe("formatIvPct — decimal IV to one-decimal vol points", () => {
  it("formats the NVDA fixture ATM", () => {
    expect(formatIvPct(0.3851329156797111)).toBe("38.5");
  });

  it("returns --- for null/undefined/non-finite", () => {
    expect(formatIvPct(null)).toBe("---");
    expect(formatIvPct(undefined)).toBe("---");
    expect(formatIvPct(Number.NaN)).toBe("---");
  });
});

describe("formatPercentile — fraction to one-decimal percent", () => {
  it("formats ranks", () => {
    expect(formatPercentile(0)).toBe("0.0%");
    expect(formatPercentile(0.05555555555555555)).toBe("5.6%");
  });

  it("returns --- for null", () => {
    expect(formatPercentile(null)).toBe("---");
  });
});

describe("formatMonthlyExpiry — third-Friday label", () => {
  it("formats the September monthly", () => {
    expect(formatMonthlyExpiry("2026-09-18")).toBe("SEP 18");
  });

  it("returns --- for null", () => {
    expect(formatMonthlyExpiry(null)).toBe("---");
  });
});

describe("isHit / volConeRegimeColor", () => {
  it("CHEAP_WINGS and CHEAP_ATM are hits", () => {
    expect(isHit("CHEAP_WINGS")).toBe(true);
    expect(isHit("CHEAP_ATM")).toBe(true);
    expect(isHit("NEUTRAL")).toBe(false);
    expect(isHit("RICH")).toBe(false);
  });

  it("maps regimes to brand tokens", () => {
    expect(volConeRegimeColor("CHEAP_WINGS")).toBe("var(--positive)");
    expect(volConeRegimeColor("CHEAP_ATM")).toBe("var(--warning)");
    expect(volConeRegimeColor("RICH")).toBe("var(--negative)");
    expect(volConeRegimeColor("NEUTRAL")).toBe("var(--text-muted)");
  });
});

describe("buildVolConeChartRows — preserves nulls", () => {
  it("maps ATM / wings and keeps gaps", () => {
    const series: VolConeSeriesPoint[] = [
      { date: "2026-08-07", spot: 220, atm_iv: 0.39, call_10_iv: 0.38, put_10_iv: 0.40 },
      { date: "2026-08-10", spot: 221, atm_iv: null, call_10_iv: 0.37, put_10_iv: 0.41 },
    ];
    const rows = buildVolConeChartRows(series);
    expect(rows.map((r) => r.date)).toEqual(["2026-08-07", "2026-08-10"]);
    expect(rows.map((r) => r.atm_iv)).toEqual([0.39, null]);
  });
});

beforeAll(() => {
  if (typeof globalThis.ResizeObserver === "undefined") {
    class StubResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    (globalThis as unknown as { ResizeObserver: typeof StubResizeObserver }).ResizeObserver =
      StubResizeObserver;
  }
});

const mockUseVolCone = vi.fn();
vi.mock("@/lib/useVolCone", () => ({
  useVolCone: (...args: unknown[]) => mockUseVolCone(...args),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

import VolConePanel from "../components/VolConePanel";

afterEach(() => {
  cleanup();
  mockUseVolCone.mockReset();
});

function point(date: string, atm: number): VolConeSeriesPoint {
  return {
    date,
    spot: 220,
    atm_iv: atm,
    call_10_iv: atm - 0.005,
    put_10_iv: atm + 0.01,
  };
}

function name(overrides: Partial<VolConeName> = {}): VolConeName {
  const series = Array.from({ length: 18 }, (_, i) =>
    point(`2026-04-${String(10 + i).padStart(2, "0")}`, 0.40 - i * 0.001),
  );
  return {
    ticker: "NVDA",
    spot: 223.95,
    expiry: "2026-09-18",
    dte: 37,
    atm_iv: 0.3851329156797111,
    call_10_iv: 0.3862120615005326,
    put_10_iv: 0.39731998999142565,
    call_10_strike: 246.345,
    put_10_strike: 201.555,
    p10: 0.3879,
    p90: 0.443,
    atm_percentile: 0,
    call_10_percentile: 0.0556,
    put_10_percentile: 0.1111,
    wing_score: 0.0833,
    regime: "CHEAP_WINGS",
    series,
    ...overrides,
  };
}

function buildData(overrides: Partial<VolConeData> = {}): VolConeData {
  const nvda = name();
  const smh = name({
    ticker: "SMH",
    regime: "NEUTRAL",
    wing_score: 0.44,
    atm_percentile: 0.4,
    atm_iv: 0.387,
  });
  return {
    scan_time: "2026-08-12T20:45:00Z",
    source_as_of: "2026-08-12",
    count: 2,
    hit_count: 1,
    current: nvda,
    names: [nvda, smh],
    hits: [nvda],
    ...overrides,
  };
}

function hookState(
  partial: Partial<{
    data: VolConeData | null;
    loading: boolean;
    syncing: boolean;
    error: string | null;
    lastSync: string | null;
  }> = {},
) {
  return {
    data: null as VolConeData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseVolCone.mockReturnValue(state);
  return render(<VolConePanel />);
}

describe("VolConePanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading UW vol cone scan")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the settled missing case", () => {
    renderPanel(
      hookState({
        data: {
          missing: true,
          scan_time: null,
          source_as_of: null,
          count: 0,
          hit_count: 0,
          current: null,
          names: [],
          hits: [],
        },
      }),
    );
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
    expect(screen.getByText("No vol cone data yet")).toBeTruthy();
  });
});

describe("VolConePanel — strip + table + chart", () => {
  it("renders hit count, best ticker, ATM, and source date", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("NVDA")).toBeTruthy();
    expect(screen.getByText("38.5")).toBeTruthy();
    expect(screen.getByText("2026-08-12")).toBeTruthy();
    expect(screen.getByText("CHEAP WINGS")).toBeTruthy();
  });

  it("renders the selected cone title", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("NVDA 2026-09-18 90/10 VOL CONE")).toBeTruthy();
  });

  it("offers ALL and HITS filter chips", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByRole("button", { name: "ALL" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "HITS" })).toBeTruthy();
  });

  it("HITS chip hides non-hit rows", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("SMH")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "HITS" }));
    expect(screen.queryByText("SMH")).toBeNull();
    expect(screen.getByText("NVDA")).toBeTruthy();
  });

  it("reorders names when a column header is clicked", () => {
    const aapl = name({ ticker: "AAPL", expiry: "2026-09-18", dte: 10, wing_score: 0.9, regime: "RICH" });
    const nvda = name({ ticker: "NVDA", expiry: "2026-09-18", dte: 37, wing_score: 0.08, regime: "CHEAP_WINGS" });
    const smh = name({ ticker: "SMH", expiry: "2026-10-16", dte: 65, wing_score: 0.44, regime: "NEUTRAL" });
    renderPanel(hookState({
      data: buildData({
        current: nvda,
        names: [nvda, smh, aapl],
        hits: [nvda],
      }),
    }));
    const section = screen.getByTestId("vol-cone-table-section");
    const firstTicker = () => section.querySelector("tbody tr td")?.textContent;
    expect(firstTicker()).toBe("NVDA");
    fireEvent.click(screen.getByRole("columnheader", { name: /ticker/i }));
    expect(firstTicker()).toBe("AAPL");
    expect(screen.getByRole("columnheader", { name: /ticker/i }).getAttribute("aria-sort")).toBe("ascending");
  });

  it("guards chart paths against NaN", () => {
    const broken = name({
      series: [
        { date: "2026-08-12", spot: Number.NaN, atm_iv: Number.NaN, call_10_iv: Number.NaN, put_10_iv: Number.NaN },
      ],
    });
    renderPanel(hookState({ data: buildData({ current: broken, names: [broken], hits: [broken] }) }));
    for (const path of document.querySelectorAll("path")) {
      expect(path.getAttribute("d") ?? "").not.toContain("NaN");
    }
  });
});

const HEX_LITERAL = /#[0-9a-fA-F]{3,8}\b/;
const EM_DASH = /\u2014/;

function tradeHref(raw: string | null): URL {
  expect(raw).toBeTruthy();
  return new URL(raw!, "http://localhost");
}

describe("VolConePanel — selected-name analysis + trade links", () => {
  it("renders operator analysis for the selected CHEAP_WINGS name", () => {
    renderPanel(hookState({ data: buildData() }));

    const analysis = screen.getByTestId("vol-cone-analysis");
    expect(analysis.textContent).toContain("LONG 10% OTM STRANGLE");
    expect(analysis.textContent).toMatch(/\$27/);
    expect(analysis.textContent).toMatch(/12%/);
    expect(analysis.textContent).toMatch(/cheap insurance/i);
    expect(analysis.textContent).toMatch(/cone/i);
    expect(analysis.textContent ?? "").not.toMatch(EM_DASH);

    const openTrade = within(analysis).getByRole("link", { name: /open trade/i });
    const href = tradeHref(openTrade.getAttribute("href"));
    expect(href.pathname).toBe("/NVDA");
    expect(href.searchParams.get("deck")).toBe("c");
    expect(href.searchParams.get("expiry")).toBe("2026-09-18");
    expect(href.searchParams.get("strikes")).toBe("100");
    expect(href.searchParams.get("src")).toBe("vol-cone");
    expect(href.searchParams.get("legs")).toBe("BUY:1x200P,BUY:1x245C");
  });

  it("makes the ticker cell a trade link only when a structure is recommended", () => {
    renderPanel(hookState({ data: buildData() }));

    const nvdaRow = screen.getByTestId("vol-cone-row-NVDA-2026-09-18");
    const nvdaLink = within(nvdaRow).getByRole("link");
    const nvdaLabel = nvdaLink.getAttribute("aria-label") ?? "";
    expect(nvdaLabel).toMatch(/open/i);
    expect(nvdaLabel).toMatch(/strangle/i);
    expect(tradeHref(nvdaLink.getAttribute("href")).searchParams.get("src")).toBe("vol-cone");
    expect(nvdaRow.querySelector("td")?.querySelector("a")).toBe(nvdaLink);

    const smhRow = screen.getByTestId("vol-cone-row-SMH-2026-09-18");
    expect(within(smhRow).queryByRole("link")).toBeNull();
  });

  it("selects a row from a non-link cell so the ticker link is not required", () => {
    renderPanel(hookState({ data: buildData() }));

    const smhRow = screen.getByTestId("vol-cone-row-SMH-2026-09-18");
    fireEvent.click(within(smhRow).getByText("37"));

    expect(screen.getByText("SMH 2026-09-18 90/10 VOL CONE")).toBeTruthy();
    const analysis = screen.getByTestId("vol-cone-analysis");
    expect(analysis.textContent).toContain("NO TRADE");
    expect(analysis.textContent).toMatch(/NEUTRAL|cone/i);
    expect(within(analysis).queryByRole("link", { name: /open trade/i })).toBeNull();

    const nvdaRow = screen.getByTestId("vol-cone-row-NVDA-2026-09-18");
    fireEvent.click(within(nvdaRow).getByText("37"));
    expect(screen.getByText("NVDA 2026-09-18 90/10 VOL CONE")).toBeTruthy();
    expect(within(screen.getByTestId("vol-cone-analysis")).getByRole("link", { name: /open trade/i })).toBeTruthy();
  });

  it("uses brand tokens and no em dashes in the analysis surface", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const analysis = screen.getByTestId("vol-cone-analysis");
    expect(analysis.innerHTML).not.toMatch(HEX_LITERAL);
    expect(container.textContent ?? "").not.toMatch(EM_DASH);
  });
});

describe("VolConePanel — live intraday sample", () => {
  it("labels the source cell LIVE when the top point is this session", () => {
    renderPanel(hookState({ data: buildData({ is_intraday: true, source_as_of: "2026-08-18" }) }));

    const cell = screen.getByTestId("vol-cone-strip-source");
    expect(within(cell).getByText("2026-08-18")).toBeTruthy();
    // Without this the tab looks identical to a stale post-close snapshot,
    // and a trader cannot tell which one they are acting on.
    expect(within(cell).getByText("LIVE THIS SESSION")).toBeTruthy();
  });

  it("keeps the completed-session label when the cone is a closing snapshot", () => {
    renderPanel(hookState({ data: buildData() }));

    const cell = screen.getByTestId("vol-cone-strip-source");
    expect(within(cell).getByText("SESSION AS OF")).toBeTruthy();
    expect(within(cell).queryByText("LIVE THIS SESSION")).toBeNull();
  });

  // T-106: the live pass refreshes only the cheap tail plus the watchlist,
  // so most rows still show last night's IV while the payload flag is true.
  it("counts live names in the strip and marks un-refreshed rows as-of", () => {
    const nvda = name({ is_intraday: true });
    const smh = name({ ticker: "SMH", regime: "NEUTRAL", wing_score: 0.44, atm_percentile: 0.4, atm_iv: 0.387 });
    renderPanel(
      hookState({
        data: buildData({
          is_intraday: true,
          source_as_of: "2026-08-18",
          current: nvda,
          names: [nvda, smh],
          hits: [nvda],
        }),
      }),
    );

    const cell = screen.getByTestId("vol-cone-strip-source");
    expect(within(cell).getByText("LIVE 1/2 NAMES")).toBeTruthy();
    expect(within(cell).queryByText("LIVE THIS SESSION")).toBeNull();

    const smhRow = screen.getByTestId("vol-cone-row-SMH-2026-09-18");
    const stale = within(smhRow).getByTestId("vol-cone-row-stale");
    expect(stale.textContent).toBe("AS OF 2026-04-27");
    expect(stale.getAttribute("aria-label")).toBe("SMH not refreshed this session, as of 2026-04-27");

    const nvdaRow = screen.getByTestId("vol-cone-row-NVDA-2026-09-18");
    expect(within(nvdaRow).queryByTestId("vol-cone-row-stale")).toBeNull();
  });

  it("keeps LIVE THIS SESSION and no row markers when every name was refreshed", () => {
    const nvda = name({ is_intraday: true });
    const smh = name({ ticker: "SMH", regime: "NEUTRAL", is_intraday: true });
    renderPanel(
      hookState({
        data: buildData({
          is_intraday: true,
          source_as_of: "2026-08-18",
          current: nvda,
          names: [nvda, smh],
          hits: [nvda],
        }),
      }),
    );

    const cell = screen.getByTestId("vol-cone-strip-source");
    expect(within(cell).getByText("LIVE THIS SESSION")).toBeTruthy();
    expect(screen.queryAllByTestId("vol-cone-row-stale")).toHaveLength(0);
  });
});
