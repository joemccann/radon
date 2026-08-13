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
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { existsSync, readFileSync } from "fs";
import { resolve } from "path";

import RegimeRelationshipView from "../components/RegimeRelationshipView";
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

afterEach(() => cleanup());

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
    overlay.getBoundingClientRect = () => ({
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
