// @vitest-environment jsdom

/**
 * CRI chart layout — full-width stack.
 *
 * The analytical history charts and the RVOL/COR1M relationship panels must
 * stack full width instead of cramming into a two-column grid.
 *
 * T-458: this contract used to slice 220 chars of component SOURCE TEXT
 * around a testid to guess the enclosing className, and to pin CSS literals
 * out of the first matching stylesheet rule. Both stayed green while a moved
 * testid or a later higher-specificity selector reproduced the regression.
 * It now renders the actual components and asserts the RENDERED DOM (ancestor
 * chain, classList) plus the WINNING cascade declaration for each rendered
 * element via the cssCascade helper.
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/regime/cri",
  useSearchParams: () => new URLSearchParams(),
  redirect: vi.fn(),
}));

// The CRI tab pulls live-sync hooks and a share modal; stub the network edges
// and the heavy D3 history chart internals. The layout under test (the grid,
// its chart slots, and the REAL RegimeRelationshipView) stays rendered.
vi.mock("../lib/useRegime", () => ({
  useRegime: () => ({ data: CRI_DATA, loading: false, syncing: false, lastSync: null }),
}));
vi.mock("../lib/useVcg", () => ({ useVcg: () => ({ data: null }) }));
vi.mock("../lib/useGex", () => ({ useGex: () => ({ data: null }) }));
vi.mock("../lib/useDispersion", () => ({ useDispersion: () => ({ data: null }) }));
vi.mock("../components/ShareReportModal", () => ({ default: () => null }));
vi.mock("../components/CriHistoryChart", () => ({ default: () => null }));

import RegimePanel from "../components/RegimePanel";
import RegimeRelationshipView, {
  nearestRegimeScatterIndex,
} from "../components/RegimeRelationshipView";
import type { RegimeRelationshipSource } from "../lib/regimeRelationships";
import { GLOBALS_CSS_RULES, winningDeclaration } from "./cssCascade";

const HISTORY = Array.from({ length: 20 }, (_, index) => ({
  date: `2026-08-${String(3 + index).padStart(2, "0")}`,
  vix: 15 + index * 0.1,
  vvix: 92 + index * 0.2,
  spy: 640 + index,
  cor1m: 18 + Math.sin(index / 3) * 4,
  realized_vol: 11 + index * 0.15,
  spx_vs_ma_pct: 1.5,
  vix_5d_roc: -2.4,
}));

const CRI_DATA = {
  scan_time: "2026-09-04T16:10:00-04:00",
  market_open: false,
  date: "2026-09-04",
  vix: 16.2,
  vvix: 95.1,
  spy: 655.2,
  vix_5d_roc: -2.4,
  vvix_vix_ratio: 5.87,
  spx_100d_ma: 6300,
  spx_distance_pct: 2.3,
  cor1m: 21.4,
  cor1m_previous_close: 20.9,
  cor1m_5d_change: 1.1,
  realized_vol: 13.2,
  cri: {
    score: 18,
    level: "LOW",
    components: { vix: 4, vvix: 5, correlation: 4, momentum: 5 },
  },
  cta: null,
  menthorq_cta: null,
  crash_trigger: {
    triggered: false,
    conditions: { spx_below_100d_ma: false, realized_vol_gt_25: false, cor1m_gt_60: false },
    values: {},
  },
  history: HISTORY,
};

const RELATIONSHIP_HISTORY: RegimeRelationshipSource[] = HISTORY.map((entry) => ({
  date: entry.date,
  realized_vol: entry.realized_vol,
  cor1m: entry.cor1m,
}));

afterEach(cleanup);

function gridColumn(element: Element): string | undefined {
  return winningDeclaration(element, ["grid-column"], GLOBALS_CSS_RULES)?.value;
}

function templateColumns(element: Element): string | undefined {
  return winningDeclaration(element, ["grid-template-columns"], GLOBALS_CSS_RULES)?.value;
}

describe("CRI chart layout — full-width stack", () => {
  it("stacks the analytical history charts in a single column", () => {
    render(<RegimePanel prices={{}} />);
    const grid = screen.getByTestId("regime-history-grid");
    expect(grid.classList.contains("regime-history-grid")).toBe(true);
    expect(within(grid).getByTestId("regime-history-chart-vix-vvix")).toBeTruthy();
    expect(within(grid).getByTestId("regime-history-chart-rvol-cor1m")).toBeTruthy();
    expect(winningDeclaration(grid, ["display"], GLOBALS_CSS_RULES)?.value).toBe("grid");
    expect(templateColumns(grid)).toBe("1fr");
  });

  it("keeps Correlation Risk Premium on its existing wide panel contract", () => {
    render(<RegimeRelationshipView history={RELATIONSHIP_HISTORY} />);
    const card = screen.getByTestId("regime-spread-card");
    expect(within(card).getByText("CORRELATION RISK PREMIUM")).toBeTruthy();
    expect(card.classList.contains("regime-relationship-panel-wide")).toBe(true);
    expect(gridColumn(card)).toBe("1 / -1");
    expect(within(card).getByTestId("regime-spread-range-chips")).toBeTruthy();
    expect(within(card).getByTestId("regime-spread-brush")).toBeTruthy();
  });

  it("makes regime quadrants and normalized divergence full-width panels", () => {
    const { container } = render(<RegimeRelationshipView history={RELATIONSHIP_HISTORY} />);
    const grid = container.querySelector(".regime-relationship-grid") as HTMLElement;
    expect(grid).toBeTruthy();
    expect(templateColumns(grid)).toBe("1fr");
    for (const testid of ["regime-quadrant-card", "regime-zscore-card"]) {
      const card = screen.getByTestId(testid);
      expect(card.parentElement).toBe(grid);
      expect(card.classList.contains("regime-relationship-panel-wide")).toBe(true);
      expect(gridColumn(card)).toBe("1 / -1");
    }
  });
});

describe("CRI quadrant scatter hover", () => {
  it("exposes hover shell, overlay, and tooltip hooks on the quadrant scatter", () => {
    render(<RegimeRelationshipView history={RELATIONSHIP_HISTORY} />);
    const card = screen.getByTestId("regime-quadrant-card");
    const shell = within(card).getByTestId("regime-quadrant-chart-shell");
    const overlay = within(shell).getByTestId("regime-quadrant-chart-overlay");
    expect(screen.queryByTestId("regime-quadrant-hover-tooltip")).toBeNull();

    const svg = within(shell).getByTestId("regime-quadrant-chart");
    svg.getBoundingClientRect = () =>
      ({ x: 0, y: 0, left: 0, top: 0, right: 760, bottom: 240, width: 760, height: 240, toJSON: () => ({}) }) as DOMRect;
    fireEvent(overlay, new MouseEvent("pointermove", { bubbles: true, clientX: 380, clientY: 120 }));

    const tooltip = within(shell).getByTestId("regime-quadrant-hover-tooltip");
    expect(within(tooltip).getByTestId("regime-quadrant-hover-date").textContent).toBeTruthy();
    const labels = Array.from(
      tooltip.querySelectorAll(".chart-tooltip-label"),
      (label) => label.textContent,
    );
    expect(labels).toContain("Quadrant");
    expect(labels).toContain("RVOL");
    expect(labels).toContain("COR1M");

    fireEvent.pointerLeave(shell);
    expect(screen.queryByTestId("regime-quadrant-hover-tooltip")).toBeNull();
  });

  it("picks the nearest scatter point in plot space", () => {
    const points = [
      { x: 10, y: 10 },
      { x: 80, y: 40 },
      { x: 120, y: 90 },
    ];
    expect(nearestRegimeScatterIndex(points, 12, 8)).toBe(0);
    expect(nearestRegimeScatterIndex(points, 78, 42)).toBe(1);
    expect(nearestRegimeScatterIndex(points, 200, 200)).toBe(2);
  });
});
