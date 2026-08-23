/**
 * @vitest-environment jsdom
 *
 * BpiPanel + BpiChart render pins against the deterministic window-relative
 * fixture (504 sessions, four UP-crossings through 30, two inside the
 * default trailing-1Y slice):
 *
 *   - index switcher chips NDX/SPX/RUT, default NDX, switch swaps payload
 *   - readout row: BPI to 1 decimal, state lozenge tone mapping (cross-up
 *     positive beats oversold warning beats neutral), members, session
 *   - chart: 30/70 threshold lines + labels, oversold wash, cross-up dot
 *     count matches the fixture within the visible slice, presets re-slice
 *   - SpectralLoader while loading, per-index empty state when missing
 */

import React from "react";
import { readFileSync } from "fs";
import { join } from "path";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import BpiPanel from "@/components/BpiPanel";
import { classifyBpiState } from "@/lib/bpi";
import {
  BPI_FIXTURE_SESSIONS,
  buildBpiPayloadFixture,
  buildBpiResponseFixture,
  buildMissingBpiIndex,
  countCrossUps,
} from "./bpi-fixture";

// jsdom doesn't ship ResizeObserver; the chart wires one up on mount.
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

const useBpiMock = vi.fn();
vi.mock("@/lib/useBpi", () => ({
  useBpi: (...args: unknown[]) => useBpiMock(...args),
}));

const DESKTOP_VIEWPORT = { isMobile: false, hasMounted: true };
const MOBILE_VIEWPORT = { isMobile: true, hasMounted: true };
const viewportMock = vi.fn(() => DESKTOP_VIEWPORT);
vi.mock("@/lib/useViewport", () => ({
  useViewport: () => viewportMock(),
}));

function hookResult(overrides: Record<string, unknown> = {}) {
  return {
    data: null,
    loading: false,
    error: null,
    refresh: vi.fn(),
    ...overrides,
  };
}

const RESPONSE = buildBpiResponseFixture();
const NDX = buildBpiPayloadFixture({ symbol: "NDX" });
const FULL_SERIES = NDX.history.map((p) => p.bpi);
const FULL_CROSS_UPS = countCrossUps(FULL_SERIES, 30);
const SLICE_1Y_CROSS_UPS = countCrossUps(FULL_SERIES.slice(-252), 30);

function crossDotCount(container: HTMLElement): number {
  return container.querySelectorAll('[data-testid="bpi-cross-dot"]').length;
}

describe("BpiPanel", () => {
  beforeEach(() => {
    useBpiMock.mockReturnValue(hookResult({ data: RESPONSE }));
  });

  afterEach(() => cleanup());

  it("fixture sanity: the series crosses 30 upward four times, twice in the trailing 1Y", () => {
    expect(NDX.history).toHaveLength(BPI_FIXTURE_SESSIONS);
    expect(FULL_CROSS_UPS).toBe(4);
    expect(SLICE_1Y_CROSS_UPS).toBe(2);
  });

  it("renders the index switcher with NDX active by default", () => {
    render(<BpiPanel />);

    for (const symbol of ["NDX", "SPX", "RUT"]) {
      expect(screen.getByTestId(`bpi-index-chip-${symbol}`)).toBeTruthy();
    }
    expect(screen.getByTestId("bpi-index-chip-NDX").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("bpi-index-chip-SPX").getAttribute("aria-pressed")).toBe("false");
  });

  it("renders the readout row: BPI to 1 decimal, members bullish/total, as-of session", () => {
    render(<BpiPanel />);

    expect(screen.getByTestId("bpi-value").textContent).toBe(NDX.bpi.toFixed(1));
    expect(screen.getByTestId("bpi-members").textContent).toBe(`${NDX.bullish} / ${NDX.members}`);
    expect(screen.getByTestId("bpi-strip-session").textContent).toContain(NDX.as_of_session);
  });

  it("maps the state lozenge: cross-up positive beats oversold warning beats neutral", () => {
    const cases: Array<[Record<string, unknown>, string, string]> = [
      [{ state: "OVERSOLD", cross_up_30: false }, "OVERSOLD", "warning"],
      [{ state: "NEUTRAL", cross_up_30: true }, "CROSS UP 30", "positive"],
      [{ state: "OVERSOLD", cross_up_30: true }, "CROSS UP 30", "positive"],
      [{ state: "NEUTRAL", cross_up_30: false }, "NEUTRAL", "neutral"],
      [{ state: "OVERBOUGHT", cross_up_30: false }, "OVERBOUGHT", "neutral"],
    ];
    for (const [patch, label, tone] of cases) {
      useBpiMock.mockReturnValue(hookResult({
        data: { ...RESPONSE, indices: { ...RESPONSE.indices, NDX: { ...NDX, ...patch } } },
      }));
      const { unmount } = render(<BpiPanel />);
      const lozenge = screen.getByTestId("bpi-state");
      expect(lozenge.textContent).toBe(label);
      expect(lozenge.getAttribute("data-tone")).toBe(tone);
      unmount();
    }
    // The lozenge mapping is the lib mapping, pinned end to end.
    expect(classifyBpiState({ state: "OVERSOLD", cross_up_30: false }).tone).toBe("warning");
  });

  it("switches payloads when an index chip is clicked", () => {
    render(<BpiPanel />);
    expect(screen.getByTestId("bpi-members").textContent).toContain("/ 100");

    fireEvent.click(screen.getByTestId("bpi-index-chip-SPX"));

    expect(screen.getByTestId("bpi-index-chip-SPX").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("bpi-members").textContent).toContain("/ 500");
  });

  it("shows the SpectralLoader while the first payload loads", () => {
    useBpiMock.mockReturnValue(hookResult({ loading: true }));
    render(<BpiPanel />);
    expect(
      screen.getByRole("status", { name: "Sampling point and figure buy signals" }),
    ).toBeTruthy();
  });

  it("renders an initial fetch failure as a measurement fault, not missing scan data", () => {
    useBpiMock.mockReturnValue(hookResult({ error: "BPI upstream unavailable" }));
    render(<BpiPanel />);

    expect(screen.getByText("Bullish percent measurement unavailable")).toBeTruthy();
    expect(screen.getByText("BPI upstream unavailable")).toBeTruthy();
    expect(screen.queryByText(/No bullish percent data yet/)).toBeNull();
  });

  it("renders the per-index empty state for a missing index and keeps the switcher usable", () => {
    useBpiMock.mockReturnValue(hookResult({
      data: {
        ...RESPONSE,
        indices: { ...RESPONSE.indices, NDX: buildMissingBpiIndex("NDX") },
      },
    }));
    render(<BpiPanel />);

    expect(screen.getByText("No bullish percent data yet for NDX")).toBeTruthy();
    expect(screen.queryByTestId("bpi-chart-section")).toBeNull();

    fireEvent.click(screen.getByTestId("bpi-index-chip-SPX"));
    expect(screen.getByTestId("bpi-members").textContent).toContain("/ 500");
  });
});

describe("BpiPanel mobile compact layout", () => {
  beforeEach(() => {
    useBpiMock.mockReturnValue(hookResult({ data: RESPONSE }));
    viewportMock.mockReturnValue(MOBILE_VIEWPORT);
  });

  afterEach(() => {
    viewportMock.mockReturnValue(DESKTOP_VIEWPORT);
    cleanup();
  });

  it("renders the index switcher as the shared m-segment segmented control, not loose chips", () => {
    render(<BpiPanel />);

    const nav = screen.getByTestId("bpi-index-chips");
    expect(nav.className).toContain("m-segment");
    expect(nav.className).not.toContain("history-range-chips");

    const ndx = screen.getByTestId("bpi-index-chip-NDX");
    expect(ndx.className).toContain("m-segment__item");
    expect(ndx.className).toContain("m-segment__item--active");
    expect(ndx.className).not.toContain("history-range-chip");
    expect(screen.getByTestId("bpi-index-chip-SPX").className).not.toContain(
      "m-segment__item--active",
    );
  });

  it("keeps aria-pressed switching behavior on the segmented control", () => {
    render(<BpiPanel />);

    fireEvent.click(screen.getByTestId("bpi-index-chip-SPX"));

    const spx = screen.getByTestId("bpi-index-chip-SPX");
    expect(spx.getAttribute("aria-pressed")).toBe("true");
    expect(spx.className).toContain("m-segment__item--active");
    expect(screen.getByTestId("bpi-mobile-grid").textContent).toContain("/ 500");
  });

  it("fuses the stat grid into the panel frame: single top hairline, no outer border doubling, no trailing sliver", () => {
    render(<BpiPanel />);

    const grid = screen.getByTestId("bpi-mobile-grid");
    // Grid keeps only a 1px top hairline; the section border frames the
    // other three edges so seams stay a single hairline.
    expect(grid.style.borderTopWidth).toBe("1px");
    expect(grid.style.borderLeftWidth).toBe("0px");
    expect(grid.style.borderRightWidth).toBe("0px");
    expect(grid.style.borderBottomWidth).toBe("0px");
    // No margin under the grid: kills the empty sliver row before the
    // section's bottom border.
    expect(["0", "0px"]).toContain(grid.style.marginBottom);
  });

  it("uses theme tokens, never raw hex, in the compact treatment", () => {
    const source = readFileSync(join(__dirname, "../components/BpiPanel.tsx"), "utf-8");
    expect(source).toContain("var(--line-grid)");
    expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});

describe("BpiPanel session staleness", () => {
  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  function renderNdx(asOf: string) {
    useBpiMock.mockReturnValue(hookResult({
      data: {
        ...RESPONSE,
        indices: { ...RESPONSE.indices, NDX: { ...NDX, as_of_session: asOf } },
      },
    }));
    return render(<BpiPanel />);
  }

  it("shows the as-of date and a visible STALE mark when the session lags lastCompletedSessionDate", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-13T19:22:00-04:00"));
    renderNdx("2026-08-12");

    const cell = screen.getByTestId("bpi-strip-session");
    expect(cell.textContent).toContain("2026-08-12");
    expect(screen.getByTestId("bpi-session-stale").textContent).toMatch(/STALE/);
    expect(cell.textContent).not.toMatch(/Refreshes nightly/i);
  });

  it("does not show STALE when as_of is the last completed session", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-13T19:22:00-04:00"));
    renderNdx("2026-08-13");

    expect(screen.getByTestId("bpi-strip-session").textContent).toContain("2026-08-13");
    expect(screen.queryByTestId("bpi-session-stale")).toBeNull();
  });
});

describe("BpiChart pins", () => {
  beforeEach(() => {
    useBpiMock.mockReturnValue(hookResult({ data: RESPONSE }));
  });

  afterEach(() => cleanup());

  it("renders the 30/70 threshold lines with labels and the oversold wash", () => {
    const { container } = render(<BpiPanel />);

    expect(container.querySelector('[data-testid="bpi-threshold-30"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="bpi-threshold-70"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="bpi-threshold-30-label"]')?.textContent).toBe(
      "30 OVERSOLD",
    );
    expect(container.querySelector('[data-testid="bpi-threshold-70-label"]')?.textContent).toBe(
      "70 OVERBOUGHT",
    );
    expect(container.querySelector('[data-testid="bpi-oversold-wash"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="bpi-line"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="bpi-last-point"]')).toBeTruthy();
  });

  it("defaults to the 1Y preset and draws the slice's cross-up dots", () => {
    const { container } = render(<BpiPanel />);

    expect(screen.getByTestId("bpi-range-chips-1y").getAttribute("aria-pressed")).toBe("true");
    expect(crossDotCount(container)).toBe(SLICE_1Y_CROSS_UPS);
  });

  it("re-slices when a preset changes: All shows every cross-up, 3M shows that window's", () => {
    const { container } = render(<BpiPanel />);

    fireEvent.click(screen.getByTestId("bpi-range-chips-all"));
    expect(crossDotCount(container)).toBe(FULL_CROSS_UPS);

    fireEvent.click(screen.getByTestId("bpi-range-chips-3m"));
    expect(crossDotCount(container)).toBe(countCrossUps(FULL_SERIES.slice(-63), 30));
  });

  it("flips the chips to Custom when the brush is dragged", () => {
    render(<BpiPanel />);
    expect(screen.queryByTestId("bpi-range-chips-custom")).toBeNull();

    const brush = screen.getByTestId("bpi-brush");
    const handle = screen.getByTestId("bpi-brush-handle-left");
    const brushRect = {
      x: 0, y: 0, left: 0, top: 0, right: 700, bottom: 40, width: 700, height: 40,
      toJSON: () => ({}),
    } as DOMRect;
    brush.getBoundingClientRect = () => brushRect;
    handle.getBoundingClientRect = () => brushRect;

    fireEvent.pointerDown(handle, { clientX: 700, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 0, pointerId: 1 });
    fireEvent.pointerUp(window, { clientX: 0, pointerId: 1 });

    expect(screen.getByTestId("bpi-range-chips-custom")).toBeTruthy();
  });

  it("shows the BPI tooltip on hover", () => {
    const { container } = render(<BpiPanel />);
    const overlay = container.querySelector('[data-testid="bpi-overlay"]');
    expect(overlay).toBeTruthy();
    fireEvent.mouseMove(overlay as Element, { clientX: 150, clientY: 60 });

    const tooltip = container.querySelector(".chart-tooltip");
    expect(tooltip?.textContent).toContain("BPI");
  });
});
