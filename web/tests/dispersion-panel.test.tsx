/**
 * @vitest-environment jsdom
 *
 * DISPERSION — VIX vs single-stock vs cross-sector dispersion, z-scored since
 * 2017 (regime tab).
 *
 * Pure helpers (lib/dispersion.ts): formatZ (signed 2dp), formatSpreadPct,
 * formatVix, regimeTone, the display constants and the frozen missing
 * contract.
 *
 * DispersionPanel: gating (SpectralLoader / SectionEmptyState), the six-cell
 * strip, the chart title, the three-entry legend, the range chips (default
 * All on the daily series back to 2017), the brush minimap, the regime tone,
 * the stale badge, the NaN guard, and copy discipline (no em dashes, no
 * cadence claims).
 *
 * Spec: docs/indicators/dispersion.md §G, §H, §M.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  COMPRESSED_Z,
  MISSING_DISPERSION,
  STRESS_Z,
  WINDOW,
  ZSCORE_BASE_START,
  formatSpreadPct,
  formatVix,
  formatZ,
  regimeTone,
  type DispersionData,
  type DispersionPoint,
} from "@/lib/dispersion";

/* ─── Pure helpers ───────────────────────────────────── */

describe("display constants mirror scripts/lib/dispersion_math.py", () => {
  it("pins the regime edges and the rolling window", () => {
    expect(STRESS_Z).toBe(1.0);
    expect(COMPRESSED_Z).toBe(-1.0);
    expect(WINDOW).toBe(60);
    expect(ZSCORE_BASE_START).toBe("2017-01-01");
  });
});

describe("formatZ", () => {
  it("renders a signed two-decimal z-score", () => {
    expect(formatZ(2.38)).toBe("+2.38");
    expect(formatZ(-0.31)).toBe("-0.31");
    expect(formatZ(2.4149)).toBe("+2.41");
    expect(formatZ(0)).toBe("+0.00");
  });

  it("renders '---' for null and NaN", () => {
    expect(formatZ(null)).toBe("---");
    expect(formatZ(Number.NaN)).toBe("---");
  });
});

describe("formatSpreadPct", () => {
  it("renders a decimal spread as a two-decimal percent", () => {
    expect(formatSpreadPct(0.0712)).toBe("7.12%");
    expect(formatSpreadPct(0.0241)).toBe("2.41%");
  });

  it("renders '---' for null and NaN", () => {
    expect(formatSpreadPct(null)).toBe("---");
    expect(formatSpreadPct(Number.NaN)).toBe("---");
  });
});

describe("formatVix", () => {
  it("renders the VIX close to two decimals", () => {
    expect(formatVix(14.43)).toBe("14.43");
    expect(formatVix(15)).toBe("15.00");
  });

  it("renders '---' for null and NaN", () => {
    expect(formatVix(null)).toBe("---");
    expect(formatVix(Number.NaN)).toBe("---");
  });
});

describe("regimeTone", () => {
  it("maps every regime to a brand token", () => {
    expect(regimeTone("BROAD STRESS")).toBe("var(--negative)");
    expect(regimeTone("BELOW THE SURFACE")).toBe("var(--warning)");
    expect(regimeTone("COMPRESSED")).toBe("var(--positive)");
    expect(regimeTone("NORMAL")).toBe("var(--text-muted)");
  });
});

describe("missing contract", () => {
  it("freezes the exact HTTP-200 missing shape", () => {
    expect(MISSING_DISPERSION).toEqual({
      missing: true,
      scan_time: null,
      status: null,
      source: null,
      data_date: null,
      universe: null,
      fetch: null,
      count: 0,
      current: null,
      stats: null,
      series: [],
    });
    expect(Object.isFrozen(MISSING_DISPERSION)).toBe(true);
  });
});

/* ─── Panel ──────────────────────────────────────────── */

// jsdom ships no ResizeObserver; the d3 chart shell wires one up on mount.
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

const mockUseDispersion = vi.fn();
vi.mock("@/lib/useDispersion", () => ({
  useDispersion: (...args: unknown[]) => mockUseDispersion(...args),
}));

import DispersionPanel from "@/components/DispersionPanel";

afterEach(() => {
  cleanup();
  mockUseDispersion.mockReset();
});

// Window-relative dates: the series always ends on the last completed
// session, never a hardcoded date. 600 points keeps the 1Y preset meaningful.
const SERIES_LENGTH = 600;

function daysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

const DATA_DATE = daysAgo(1);

const SECTORS = ["XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"];

function buildSeries(length = SERIES_LENGTH): DispersionPoint[] {
  return Array.from({ length }, (_, i) => ({
    date: daysAgo(length - i),
    // Smooth z-scores inside a plausible -2..+3 band; never NaN.
    z_vix: 0.4 + 1.2 * Math.sin(i / 20),
    z_stock: 0.8 + 1.5 * Math.sin(i / 25),
    z_sector: 0.6 + 1.4 * Math.sin(i / 30),
    vix: 16 + 4 * Math.sin(i / 20),
    stock_spread: 0.06 + 0.02 * Math.sin(i / 25),
    sector_spread: 0.02 + 0.008 * Math.sin(i / 30),
  }));
}

const CURRENT = {
  date: DATA_DATE,
  z_vix: -0.31,
  z_stock: 2.38,
  z_sector: 2.41,
  vix: 14.43,
  stock_spread: 0.0712,
  sector_spread: 0.0241,
  m60_vix: 15.9,
  m60_stock: 0.0834,
  m60_sector: 0.0302,
  n_stocks: 501,
  n_sectors: 11,
  regime: "BELOW THE SURFACE" as const,
  surface_gap: 2.72,
};

function buildData(overrides: Partial<DispersionData> = {}): DispersionData {
  return {
    scan_time: new Date().toISOString(),
    status: "ok",
    source: { prices: "ib", vix: "ib" },
    data_date: DATA_DATE,
    universe: { index: "SPX", n_constituents: 503, sectors: SECTORS },
    fetch: { ib_ok: 512, yahoo_ok: 2, failed: 1, failed_symbols: ["FOO"] },
    count: SERIES_LENGTH,
    current: { ...CURRENT },
    stats: {
      base: { start: "2017-01-03", end: DATA_DATE, n: SERIES_LENGTH },
      vix: { mean_60d: 18.9, stdev_60d: 6.1, z_min: -1.2, z_max: 5.3 },
      stock: { mean_60d: 0.061, stdev_60d: 0.014, z_min: -1.4, z_max: 4.1 },
      sector: { mean_60d: 0.019, stdev_60d: 0.006, z_min: -1.3, z_max: 3.9 },
      days_below_surface: 214,
      last_below_surface_date: DATA_DATE,
    },
    series: [
      ...buildSeries().slice(0, -1),
      {
        date: DATA_DATE,
        z_vix: CURRENT.z_vix,
        z_stock: CURRENT.z_stock,
        z_sector: CURRENT.z_sector,
        vix: CURRENT.vix,
        stock_spread: CURRENT.stock_spread,
        sector_spread: CURRENT.sector_spread,
      },
    ],
    ...overrides,
  };
}

function hookState(
  partial: Partial<{
    data: DispersionData | null;
    loading: boolean;
    syncing: boolean;
    error: string | null;
    lastSync: string | null;
  }> = {},
) {
  return {
    data: null as DispersionData | null,
    loading: false,
    syncing: false,
    error: null as string | null,
    lastSync: null as string | null,
    syncNow: vi.fn(),
    ...partial,
  };
}

function renderPanel(state: ReturnType<typeof hookState>) {
  mockUseDispersion.mockReturnValue(state);
  return render(<DispersionPanel />);
}

function pathData(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("path[d]")).map((p) => p.getAttribute("d") ?? "");
}

describe("DispersionPanel — gating", () => {
  it("shows the SpectralLoader while the first payload is loading", () => {
    renderPanel(hookState({ loading: true }));
    expect(screen.getByText("Loading dispersion series")).toBeTruthy();
  });

  it("shows the SpectralLoader while syncing with no data yet", () => {
    renderPanel(hookState({ syncing: true }));
    expect(screen.getByText("Loading dispersion series")).toBeTruthy();
  });

  it("shows the SectionEmptyState on the missing contract", () => {
    renderPanel(hookState({ data: { ...MISSING_DISPERSION } as unknown as DispersionData }));
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
  });

  it("shows the SectionEmptyState when current is null", () => {
    renderPanel(hookState({ data: buildData({ current: null }) }));
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
  });

  it("shows the SectionEmptyState when the hook has no data", () => {
    renderPanel(hookState());
    expect(screen.getByTestId("section-empty-state")).toBeTruthy();
  });

  // R-450: the route's staleCollapse keeps `stale: true` + the last scan_time;
  // "died Tuesday" must not render as "never seeded".
  it("renders the writer-stale state with the last scan_time, not the empty state", () => {
    const scanTime = "2026-08-25T22:21:07Z";
    renderPanel(
      hookState({
        data: { ...MISSING_DISPERSION, stale: true, scan_time: scanTime } as unknown as DispersionData,
      }),
    );
    const stale = screen.getByTestId("dispersion-writer-stale");
    expect(stale.textContent ?? "").toContain(scanTime);
    expect(stale.textContent ?? "").toMatch(/stale/i);
    expect(stale.textContent ?? "").not.toMatch(/No dispersion data yet/);
    expect(screen.queryByTestId("section-empty-state")).toBeNull();
  });
});

describe("DispersionPanel — header strip", () => {
  it("renders the six strip values", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("dispersion-regime").textContent).toBe("BELOW THE SURFACE");
    expect(screen.getByTestId("dispersion-z-stock").textContent).toBe("+2.38");
    expect(screen.getByTestId("dispersion-z-sector").textContent).toBe("+2.41");
    expect(screen.getByTestId("dispersion-z-vix").textContent).toBe("-0.31");
    expect(screen.getByTestId("dispersion-gap").textContent).toBe("+2.72");
    expect(screen.getByTestId("dispersion-source-updated").textContent).toBe(DATA_DATE);
  });

  it("renders the strip sub-lines off the payload", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const text = container.textContent ?? "";
    expect(text).toContain("95-5 spread 7.12%");
    expect(text).toContain("95-5 spread 2.41%");
    expect(text).toContain("14.43");
    expect(text).toContain("501 stocks / 11 sectors");
    expect(text).toContain("stock 2.38 / sector 2.41 / VIX -0.31");
  });

  it("renders a negative gap and a BROAD STRESS regime", () => {
    const data = buildData();
    data.current = {
      ...data.current!,
      z_vix: 2.1,
      z_stock: 0.5,
      z_sector: 0.2,
      regime: "BROAD STRESS",
      surface_gap: -1.6,
    };
    renderPanel(hookState({ data }));
    expect(screen.getByTestId("dispersion-regime").textContent).toBe("BROAD STRESS");
    expect(screen.getByTestId("dispersion-z-vix").textContent).toBe("+2.10");
    expect(screen.getByTestId("dispersion-gap").textContent).toBe("-1.60");
  });

  it("renders '---' cells when the current z-scores are null", () => {
    const data = buildData();
    data.current = {
      ...data.current!,
      z_stock: null as unknown as number,
      z_vix: null as unknown as number,
    };
    renderPanel(hookState({ data }));
    expect(screen.getByTestId("dispersion-z-stock").textContent).toBe("---");
    expect(screen.getByTestId("dispersion-z-vix").textContent).toBe("---");
  });
});

describe("DispersionPanel — regime tone", () => {
  it.each([
    ["BROAD STRESS", "var(--negative)"],
    ["BELOW THE SURFACE", "var(--warning)"],
    ["COMPRESSED", "var(--positive)"],
    ["NORMAL", "var(--text-muted)"],
  ] as const)("tones %s with %s", (regime, token) => {
    const data = buildData();
    data.current = { ...data.current!, regime };
    renderPanel(hookState({ data }));
    const cell = screen.getByTestId("dispersion-regime") as HTMLElement;
    expect(cell.style.color).toBe(token);
  });
});

describe("DispersionPanel — stale badge", () => {
  it("renders the SOURCE STALE badge on a stale_source payload", () => {
    renderPanel(hookState({ data: buildData({ status: "stale_source" }) }));
    const badge = screen.getByTestId("dispersion-source-stale");
    expect(badge.textContent).toContain("SOURCE STALE");
    expect(badge.getAttribute("title") ?? badge.textContent ?? "").toContain(
      "re-serving the last confirmed series",
    );
  });

  it("omits the badge on a healthy payload", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.queryByTestId("dispersion-source-stale")).toBeNull();
  });
});

// R-434: the strip names the rung that served the prices from
// payload.source.prices, and a Yahoo-built sweep carries a visible degraded
// marker (CLAUDE.md rule 7: Yahoo is the last rung, never a silent primary).
describe("DispersionPanel — price source", () => {
  it("renders the IB source label in the strip with no degraded marker", () => {
    renderPanel(hookState({ data: buildData({ source: { prices: "ib", vix: "ib" } }) }));
    expect(screen.getByTestId("dispersion-source").textContent).toContain("IB");
    expect(screen.queryByTestId("dispersion-source-degraded")).toBeNull();
  });

  it("renders a visible degraded marker when Yahoo served the sweep", () => {
    renderPanel(
      hookState({
        data: buildData({
          source: { prices: "yahoo", vix: "yahoo" },
          fetch: { ib_ok: 0, yahoo_ok: 514, failed: 0, failed_symbols: [] },
        }),
      }),
    );
    expect(screen.getByTestId("dispersion-source").textContent).toContain("YAHOO");
    const badge = screen.getByTestId("dispersion-source-degraded") as HTMLElement;
    expect(badge.textContent).toContain("YAHOO");
    expect(badge.style.color).toBe("var(--warning)");
    expect(badge.getAttribute("title") ?? "").toMatch(/Interactive Brokers/);
  });

  it("names Yahoo in the footnote when Yahoo served the sweep", () => {
    const { container } = renderPanel(
      hookState({ data: buildData({ source: { prices: "yahoo", vix: "yahoo" } }) }),
    );
    expect(container.textContent ?? "").toMatch(/Source: Yahoo/);
  });

  it("labels a mixed sweep and counts the Yahoo symbols in the footnote", () => {
    const { container } = renderPanel(
      hookState({
        data: buildData({
          source: { prices: "mixed", vix: "ib" },
          fetch: { ib_ok: 400, yahoo_ok: 114, failed: 0, failed_symbols: [] },
        }),
      }),
    );
    expect(screen.getByTestId("dispersion-source").textContent).toContain("IB + YAHOO");
    expect(screen.queryByTestId("dispersion-source-degraded")).toBeNull();
    expect(container.textContent ?? "").toMatch(/Yahoo Finance fallback for 114 symbols/);
  });
});

describe("DispersionPanel — chart + controls", () => {
  it("renders the chart title", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByText("VOLATILITY DISPERSION - Z-SCORE SINCE 2017")).toBeTruthy();
  });

  it("renders three legend entries, one per line", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const items = Array.from(container.querySelectorAll(".chart-legend-item"));
    expect(items.length).toBe(3);
    const labels = items.map((i) => (i.textContent ?? "").toLowerCase());
    expect(labels.some((l) => l.includes("vix"))).toBe(true);
    expect(labels.some((l) => l.includes("stock"))).toBe(true);
    expect(labels.some((l) => l.includes("sector"))).toBe(true);
  });

  it("defaults the range chips to All on the daily series", () => {
    renderPanel(hookState({ data: buildData() }));
    const all = screen.getByRole("button", { name: "All" });
    expect(all.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "1Y" })).toBeTruthy();
  });

  it("renders the brush minimap with the dispersion testid prefix", () => {
    renderPanel(hookState({ data: buildData() }));
    expect(screen.getByTestId("dispersion-brush")).toBeTruthy();
    expect(screen.getByTestId("dispersion-brush-window")).toBeTruthy();
  });

  it("draws at least three stroked line paths", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const lines = pathData(container).filter((d) => d.includes("L"));
    expect(lines.length).toBeGreaterThanOrEqual(3);
  });

  it("never emits NaN into chart paths across the daily domain", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const paths = pathData(container);
    expect(paths.length).toBeGreaterThan(0);
    for (const d of paths) {
      expect(d).not.toContain("NaN");
    }
  });

  it("tolerates a null z point without emitting NaN", () => {
    const data = buildData();
    data.series = data.series.map((p, i) =>
      i > data.series.length - 9 ? { ...p, z_sector: null as unknown as number } : p,
    );
    const { container } = renderPanel(hookState({ data }));
    const paths = pathData(container);
    expect(paths.length).toBeGreaterThan(0);
    for (const d of paths) {
      expect(d).not.toContain("NaN");
    }
  });

  it("renders a single-point series without a brush and without NaN", () => {
    const data = buildData({ series: buildData().series.slice(-1), count: 1 });
    const { container } = renderPanel(hookState({ data }));
    expect(screen.queryByTestId("dispersion-brush")).toBeNull();
    for (const d of pathData(container)) {
      expect(d).not.toContain("NaN");
    }
  });
});

describe("DispersionPanel — copy discipline", () => {
  it("contains no em dashes and no cadence claims in its copy", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const text = container.textContent ?? "";
    expect(text).not.toContain("—");
    expect(text).not.toMatch(/refresh(es)? (daily|hourly|every)|updated (daily|hourly|every)/i);
  });

  it("names the source and the proxies in the footnote", () => {
    const { container } = renderPanel(hookState({ data: buildData() }));
    const text = container.textContent ?? "";
    expect(text).toMatch(/Interactive Brokers/);
  });
});
