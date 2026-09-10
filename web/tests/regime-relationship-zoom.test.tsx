// @vitest-environment jsdom

/**
 * Tests for zoom + pan controls on the Correlation Risk Premium chart.
 *
 * Covers:
 *   - Default range covers the last 252 trading days (or all when shorter).
 *   - Preset chips (1M/3M/6M/1Y/All) narrow rendered bars to the right count.
 *   - Brush start/end state propagates to the chart's visible slice.
 *   - Tooltip exposes correct values for an in-range hover.
 *   - No raw hex in any new className/style.
 *   - Pointer-event interaction with the brush handles updates state.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { existsSync, readFileSync } from "fs";
import { resolve } from "path";

import RegimeRelationshipView, { nearestRegimeScatterIndex } from "../components/RegimeRelationshipView";
import type { RegimeRelationshipSource } from "../lib/regimeRelationships";

// Resolve from the vitest cwd. Run-from-web/ and run-from-repo-root both work.
const VIEW_PATH = (() => {
  const candidates = [
    resolve(process.cwd(), "web/components/RegimeRelationshipView.tsx"),
    resolve(process.cwd(), "components/RegimeRelationshipView.tsx"),
    resolve(process.cwd(), "../web/components/RegimeRelationshipView.tsx"),
  ];
  const found = candidates.find((path) => existsSync(path));
  if (!found) {
    throw new Error(
      `Could not locate RegimeRelationshipView.tsx from cwd=${process.cwd()}`,
    );
  }
  return found;
})();

function buildHistory(length: number): RegimeRelationshipSource[] {
  const out: RegimeRelationshipSource[] = [];
  // Start far enough back to make a 300+ session span deterministic.
  const startMs = new Date("2024-01-02T00:00:00Z").getTime();
  for (let index = 0; index < length; index += 1) {
    const ts = new Date(startMs + index * 86_400_000);
    const yyyy = ts.getUTCFullYear();
    const mm = String(ts.getUTCMonth() + 1).padStart(2, "0");
    const dd = String(ts.getUTCDate()).padStart(2, "0");
    out.push({
      date: `${yyyy}-${mm}-${dd}`,
      realized_vol: 10 + index * 0.01,
      cor1m: 12 + Math.sin(index / 7) * 2 + index * 0.005,
    });
  }
  return out;
}

function countSpreadBars(): number {
  return document.querySelectorAll(
    '[data-testid="regime-spread-chart"] rect[data-testid^="regime-spread-bar-"]',
  ).length;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Correlation Risk Premium preset range chips", () => {
  it("defaults the visible range to the last 252 trading sessions when history is longer", () => {
    const history = buildHistory(300);
    render(
      React.createElement(RegimeRelationshipView, {
        history,
      }),
    );

    expect(countSpreadBars()).toBe(252);
    const activeChip = screen.getByTestId("regime-spread-range-1y");
    expect(activeChip.getAttribute("data-active")).toBe("true");
  });

  it("shows every bar when history is shorter than a year and keeps the 1Y chip selected", () => {
    const history = buildHistory(40);
    render(
      React.createElement(RegimeRelationshipView, {
        history,
      }),
    );

    expect(countSpreadBars()).toBe(40);
    const oneYear = screen.getByTestId("regime-spread-range-1y");
    expect(oneYear.getAttribute("data-active")).toBe("true");
  });

  it("narrows rendered bars to the right session count when a preset chip is selected", () => {
    const history = buildHistory(300);
    render(
      React.createElement(RegimeRelationshipView, {
        history,
      }),
    );

    fireEvent.click(screen.getByTestId("regime-spread-range-1m"));
    expect(countSpreadBars()).toBe(21);

    fireEvent.click(screen.getByTestId("regime-spread-range-3m"));
    expect(countSpreadBars()).toBe(63);

    fireEvent.click(screen.getByTestId("regime-spread-range-6m"));
    expect(countSpreadBars()).toBe(126);

    fireEvent.click(screen.getByTestId("regime-spread-range-all"));
    expect(countSpreadBars()).toBe(300);
  });

  it("keeps an active preset anchored to newly appended sessions", () => {
    const { rerender } = render(<RegimeRelationshipView history={buildHistory(40)} />);
    fireEvent.click(screen.getByTestId("regime-spread-range-1m"));
    expect(countSpreadBars()).toBe(21);
    rerender(<RegimeRelationshipView history={buildHistory(45)} />);
    expect(countSpreadBars()).toBe(21);
    expect(screen.getByTestId("regime-spread-range-1m").getAttribute("data-active")).toBe("true");
  });

  it("renders mono-font preset chips for each available range", () => {
    const history = buildHistory(120);
    render(
      React.createElement(RegimeRelationshipView, {
        history,
      }),
    );

    const chipRow = screen.getByTestId("regime-spread-range-chips");
    expect(chipRow).toBeTruthy();
    ["1m", "3m", "6m", "1y", "all"].forEach((slug) => {
      expect(screen.getByTestId(`regime-spread-range-${slug}`)).toBeTruthy();
    });
  });

  it("does not introduce raw hex colors in the regime relationship view source", () => {
    const source = readFileSync(VIEW_PATH, "utf-8");
    const hexMatches = source.match(/#(?:[0-9a-fA-F]{3}){1,2}\b/g);
    expect(hexMatches).toBeNull();
  });
});

describe("Correlation Risk Premium brush + hover", () => {
  it("exposes a hover tooltip with date, spread, and z-score values for the in-range entry", () => {
    const history = buildHistory(40);
    render(
      React.createElement(RegimeRelationshipView, {
        history,
      }),
    );

    const overlay = screen.getByTestId("regime-spread-chart-overlay");
    screen.getByTestId("regime-spread-chart").getBoundingClientRect = () => ({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 760,
      bottom: 240,
      width: 760,
      height: 240,
      toJSON: () => ({}),
    });

    fireEvent.pointerMove(overlay, { clientX: 400, clientY: 100 });

    const tooltip = screen.getByTestId("regime-spread-hover-tooltip");
    expect(tooltip).toBeTruthy();
    expect(screen.getByTestId("regime-spread-hover-date")).toBeTruthy();
    expect(tooltip.textContent ?? "").toMatch(/Spread/i);
    expect(tooltip.textContent ?? "").toMatch(/RVOL z/i);
    expect(tooltip.textContent ?? "").toMatch(/COR1M z/i);
    expect(tooltip.textContent ?? "").toMatch(/(Goldilocks|Fragile Calm|Stock Picker|Systemic Panic)/i);
  });

  it("renders a brush minimap beneath the chart with two draggable handles", () => {
    const history = buildHistory(120);
    render(
      React.createElement(RegimeRelationshipView, {
        history,
      }),
    );

    expect(screen.getByTestId("regime-spread-brush")).toBeTruthy();
    expect(screen.getByTestId("regime-spread-brush-handle-left")).toBeTruthy();
    expect(screen.getByTestId("regime-spread-brush-handle-right")).toBeTruthy();
    expect(screen.getByTestId("regime-spread-brush-window")).toBeTruthy();
  });

  it("propagates brush window state to the chart's visible slice on pointer drag", () => {
    const history = buildHistory(252);
    render(
      React.createElement(RegimeRelationshipView, {
        history,
      }),
    );

    const brush = screen.getByTestId("regime-spread-brush");
    const handle = screen.getByTestId("regime-spread-brush-handle-left");

    const brushRect = {
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 700,
      bottom: 40,
      width: 700,
      height: 40,
      toJSON: () => ({}),
    };
    brush.getBoundingClientRect = () => brushRect;
    handle.getBoundingClientRect = () => brushRect;

    const before = countSpreadBars();
    expect(before).toBe(252);

    fireEvent.pointerDown(handle, { clientX: 0, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 350, pointerId: 1 });
    fireEvent.pointerUp(window, { clientX: 350, pointerId: 1 });

    const after = countSpreadBars();
    expect(after).toBeLessThan(before);
    expect(after).toBeGreaterThan(0);

    // Chips should swap to the Custom label after a manual brush drag.
    expect(screen.getByTestId("regime-spread-range-custom")).toBeTruthy();
  });

  it("snaps the brush window to the preset range when a chip is clicked", () => {
    const history = buildHistory(252);
    render(
      React.createElement(RegimeRelationshipView, {
        history,
      }),
    );

    fireEvent.click(screen.getByTestId("regime-spread-range-1m"));
    expect(countSpreadBars()).toBe(21);

    const windowEl = screen.getByTestId("regime-spread-brush-window");
    const widthPct = windowEl.style.width;
    // 21 of 252 sessions is ~8.3% of the brush track.
    expect(widthPct).toMatch(/%/);
    const widthVal = Number.parseFloat(widthPct);
    expect(widthVal).toBeGreaterThan(5);
    expect(widthVal).toBeLessThan(15);
  });
});

describe("Regime quadrants scatter hover", () => {
  const history: RegimeRelationshipSource[] = [
    { date: "2026-03-02", realized_vol: 10, cor1m: 10 },
    { date: "2026-03-03", realized_vol: 20, cor1m: 40 },
    { date: "2026-03-04", realized_vol: 30, cor1m: 20 },
  ];

  it.each([760, 380])("shows the exact nearest session at a rendered width of %ipx", (width) => {
    render(<RegimeRelationshipView history={history} />);
    const svg = screen.getByTestId("regime-quadrant-chart");
    const overlay = screen.getByTestId("regime-quadrant-chart-overlay");
    const height = width * 240 / 760;
    svg.getBoundingClientRect = () => ({
      x: 100,
      y: 200,
      left: 100,
      top: 200,
      right: 100 + width,
      bottom: 200 + height,
      width,
      height,
      toJSON: () => ({}),
    });
    const point = screen.getByTestId("regime-quadrant-point-2026-03-03");
    const clientX = 100 + (Number(point.getAttribute("cx")) + 44) * width / 760;
    const clientY = 200 + (Number(point.getAttribute("cy")) + 16) * height / 240;
    // Construct a coordinate-bearing event without depending on jsdom's
    // optional PointerEvent implementation.
    fireEvent(overlay, new MouseEvent("pointermove", { bubbles: true, clientX, clientY }));

    const tooltip = screen.getByTestId("regime-quadrant-hover-tooltip");
    expect(screen.getByTestId("regime-quadrant-hover-date").textContent).toBe("Mar 3");
    expect(Array.from(tooltip.querySelectorAll(".chart-tooltip-row"), (row) => row.textContent)).toEqual([
      "QuadrantSystemic Panic",
      "RVOL20.00",
      "COR1M40.00",
      "RVOL z+0.00σ",
      "COR1M z+1.09σ",
    ]);

    fireEvent.pointerLeave(screen.getByTestId("regime-quadrant-chart-shell"));
    expect(screen.queryByTestId("regime-quadrant-hover-tooltip")).toBeNull();
    expect(document.querySelector(".regime-relationship-hover-marker")).toBeNull();
  });

  it("returns a safe index for empty and single-point inputs and keeps the first distance tie", () => {
    expect(nearestRegimeScatterIndex([], 10, 20)).toBe(0);
    expect(nearestRegimeScatterIndex([{ x: 80, y: 40 }], -100, 200)).toBe(0);
    expect(nearestRegimeScatterIndex([{ x: 0, y: 0 }, { x: 10, y: 0 }], 5, 0)).toBe(0);
  });

  it.each([{ entries: [] }, { entries: history.slice(0, 1) }])("does not render an interactive chart without two comparable sessions", ({ entries }) => {
    render(<RegimeRelationshipView history={entries} />);
    expect(screen.queryByTestId("regime-quadrant-chart")).toBeNull();
    expect(screen.queryByTestId("regime-quadrant-hover-tooltip")).toBeNull();
  });
});

describe("Regime relationship responsive coordinates", () => {
  function mockChartResize(initialWidth: number) {
    let width = initialWidth;
    let resize: (() => void) | undefined;
    const observe = vi.fn();
    const disconnect = vi.fn();
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: () => void) { resize = callback; }
      observe = observe;
      disconnect = disconnect;
    });
    const originalRect = HTMLElement.prototype.getBoundingClientRect;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.getAttribute("data-testid") !== "regime-spread-chart-shell") {
        return originalRect.call(this);
      }
      return { x: 0, y: 0, left: 0, top: 0, right: width, bottom: 240, width, height: 240, toJSON: () => ({}) };
    });
    return {
      observe,
      disconnect,
      resize(nextWidth: number) {
        width = nextWidth;
        act(() => resize?.());
      },
    };
  }

  function expectViewBoxes(width: number) {
    for (const chart of ["spread", "quadrant", "zscore"]) {
      expect(screen.getByTestId(`regime-${chart}-chart`).getAttribute("viewBox")).toBe(`0 0 ${width} 240`);
    }
  }

  it("uses the measured shell width for all chart coordinates and preserves the selected brush range on resize", () => {
    const observer = mockChartResize(260);
    const { unmount } = render(<RegimeRelationshipView history={buildHistory(300)} />);
    expect(observer.observe).toHaveBeenCalledWith(screen.getByTestId("regime-spread-chart-shell"));
    expectViewBoxes(260);
    const firstPoint = screen.getByTestId("regime-quadrant-point-2024-01-02");
    const narrowPointX = Number(firstPoint.getAttribute("cx"));
    expect(narrowPointX).toBeGreaterThan(0);
    expect(narrowPointX).toBeLessThan(260 - 64);
    expect(screen.getByTestId("regime-spread-brush").querySelector("svg")?.getAttribute("viewBox")).toBe("0 0 760 40");
    fireEvent.click(screen.getByTestId("regime-spread-range-1m"));
    expect(countSpreadBars()).toBe(21);
    const spread = screen.getByTestId("regime-current-spread").textContent;
    const quadrant = screen.getByTestId("regime-current-quadrant").textContent;

    observer.resize(1024);
    expectViewBoxes(1024);
    expect(Number(firstPoint.getAttribute("cx")) / narrowPointX).toBeCloseTo((1024 - 64) / (260 - 64));
    expect(countSpreadBars()).toBe(21);
    expect(screen.getByTestId("regime-current-spread").textContent).toBe(spread);
    expect(screen.getByTestId("regime-current-quadrant").textContent).toBe(quadrant);
    observer.resize(0);
    expectViewBoxes(1024);
    unmount();
    expect(observer.disconnect).toHaveBeenCalledOnce();
  });

  it("starts measurement when initially missing chart data becomes available", () => {
    const observer = mockChartResize(390);
    const { rerender } = render(<RegimeRelationshipView history={[]} />);
    expect(observer.observe).not.toHaveBeenCalled();
    rerender(<RegimeRelationshipView history={buildHistory(40)} />);
    expectViewBoxes(390);
    expect(observer.observe).toHaveBeenCalledOnce();
  });

  it.each([
    { width: 0, height: 240 },
    { width: 760, height: 0 },
  ])("does not resolve hover from zero-sized SVG geometry ($width x $height)", ({ width, height }) => {
    render(<RegimeRelationshipView history={buildHistory(40)} />);
    for (const chart of ["spread", "quadrant", "zscore"]) {
      screen.getByTestId(`regime-${chart}-chart`).getBoundingClientRect = () => ({
        x: 0, y: 0, left: 0, top: 0, right: width, bottom: height, width, height, toJSON: () => ({}),
      });
      fireEvent(screen.getByTestId(`regime-${chart}-chart-overlay`), new MouseEvent("pointermove", {
        bubbles: true, clientX: 100, clientY: 100,
      }));
      expect(screen.queryByTestId(`regime-${chart}-hover-tooltip`)).toBeNull();
    }
  });
});
