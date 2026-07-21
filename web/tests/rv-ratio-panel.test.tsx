/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import RvRatioPanel from "@/components/RvRatioPanel";
import OptionsWorkspacePanel from "@/components/OptionsWorkspacePanel";
import { classifyRatioRegime, type RvRatioDivergence } from "@/lib/rvRatio";
import {
  buildCompleteRvRatioFixture,
  buildPartialRvRatioFixture,
} from "./rv-ratio-fixture";

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

const useRvRatioMock = vi.fn();
vi.mock("@/lib/useRvRatio", () => ({
  useRvRatio: (...args: unknown[]) => useRvRatioMock(...args),
}));

const useOptionsExposureMock = vi.fn();
vi.mock("@/lib/useOptionsExposure", () => ({
  useOptionsExposure: (...args: unknown[]) => useOptionsExposureMock(...args),
}));

const pushMock = vi.fn();
let mockPathname = "/options/rv-ratio";
vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ push: pushMock }),
}));

function hookResult(overrides: Record<string, unknown> = {}) {
  return {
    data: null,
    loading: false,
    scanning: false,
    error: null,
    refresh: vi.fn(),
    ...overrides,
  };
}

const COMPLETE = buildCompleteRvRatioFixture();
const PARTIAL = buildPartialRvRatioFixture();

describe("RvRatioPanel", () => {
  beforeEach(() => {
    useRvRatioMock.mockReturnValue(hookResult({ data: COMPLETE }));
    useOptionsExposureMock.mockReturnValue({ data: null, loading: true, error: null, refresh: vi.fn() });
    pushMock.mockReset();
    mockPathname = "/options/rv-ratio";
  });

  afterEach(() => cleanup());

  it("renders the header with symbol readout, regime badge, and telemetry line", () => {
    render(<RvRatioPanel symbol="SMH" />);

    expect(screen.getByRole("heading", { name: "Realized Vol Ratio" })).toBeTruthy();
    expect(screen.getByText("OPTIONS / RELATIVE VOL")).toBeTruthy();
    expect(screen.getByText("SMH / SPY")).toBeTruthy();

    const badge = screen.getByTestId("rv-ratio-regime-badge");
    const expected = classifyRatioRegime(COMPLETE.stats);
    expect(badge.textContent).toBe(expected.label);
    expect(badge.getAttribute("data-tone")).toBe(expected.tone);

    const telemetry = screen.getByTestId("rv-ratio-telemetry").textContent ?? "";
    expect(telemetry).toContain(`AS OF ${COMPLETE.coverage.last_date} CLOSE`);
    expect(telemetry).toContain("252-SESSION RV");
    expect(telemetry).toContain("2,520 SESSIONS");
    expect(telemetry).toContain("BENCHMARK SPY");
  });

  it("renders six stat tiles with fixture-derived values and the full-history footnote", () => {
    render(<RvRatioPanel symbol="SMH" />);

    const stats = COMPLETE.stats;
    expect(screen.getByTestId("rv-ratio-stat-high").textContent).toContain(`${stats.high.toFixed(2)}x`);
    expect(screen.getByTestId("rv-ratio-stat-low").textContent).toContain(`${stats.low.toFixed(2)}x`);
    expect(screen.getByTestId("rv-ratio-stat-avg").textContent).toContain(`${stats.avg.toFixed(2)}x`);
    expect(screen.getByTestId("rv-ratio-stat-last").textContent).toContain(`${stats.last.toFixed(2)}x`);
    expect(screen.getByTestId("rv-ratio-stat-stddev").textContent).toContain(stats.stddev.toFixed(2));
    expect(screen.getByTestId("rv-ratio-stat-pctl").textContent).toContain(
      `${Math.round(stats.last_percentile * 100)}%`,
    );
    // LAST carries the divergence tone.
    expect(screen.getByTestId("rv-ratio-stat-last").getAttribute("data-tone")).toBe(
      classifyRatioRegime(stats).tone,
    );
    expect(screen.getByText("Stats span the full loaded history, not the visible range.")).toBeTruthy();
  });

  it("maps every divergence regime to its badge label and tone", () => {
    const cases: Array<[RvRatioDivergence, string, string]> = [
      ["in_band", "IN BAND", "neutral"],
      ["elevated", "ELEVATED", "caution"],
      ["decoupled", "DECOUPLED", "dislocation"],
      ["compressed", "COMPRESSED", "neutral-low"],
    ];
    for (const [divergence, label, tone] of cases) {
      useRvRatioMock.mockReturnValue(hookResult({
        data: { ...COMPLETE, stats: { ...COMPLETE.stats, divergence } },
      }));
      const { unmount } = render(<RvRatioPanel symbol="SMH" />);
      const badge = screen.getByTestId("rv-ratio-regime-badge");
      expect(badge.textContent).toBe(label);
      expect(badge.getAttribute("data-tone")).toBe(tone);
      unmount();
    }
  });

  it("shows the PARTIAL badge only for incomplete coverage", () => {
    useRvRatioMock.mockReturnValue(hookResult({ data: PARTIAL }));
    const { unmount } = render(<RvRatioPanel symbol="SMH" />);
    expect(screen.getByText("PARTIAL")).toBeTruthy();
    unmount();

    useRvRatioMock.mockReturnValue(hookResult({ data: COMPLETE }));
    render(<RvRatioPanel symbol="SMH" />);
    expect(screen.queryByText("PARTIAL")).toBeNull();
  });

  it("renders loading, cold-symbol, error, and insufficient-history states", () => {
    useRvRatioMock.mockReturnValue(hookResult({ loading: true }));
    const { rerender } = render(<RvRatioPanel symbol="SMH" />);
    expect(screen.getByRole("status", { name: "Sampling 252-session realized vol" })).toBeTruthy();

    // Cold symbol: missing snapshot fired the scan POST; copy sets the backfill expectation.
    useRvRatioMock.mockReturnValue(hookResult({ scanning: true }));
    rerender(<RvRatioPanel symbol="SMH" />);
    expect(
      screen.getByRole("status", { name: "First measurement. Fetching 12 years of closes" }),
    ).toBeTruthy();

    const refresh = vi.fn();
    useRvRatioMock.mockReturnValue(hookResult({ error: "Scan failed", refresh }));
    rerender(<RvRatioPanel symbol="SMH" />);
    expect(screen.getByRole("alert").textContent).toContain("Scan failed");
    fireEvent.click(screen.getByRole("button", { name: "Retry measurement" }));
    expect(refresh).toHaveBeenCalledTimes(1);

    useRvRatioMock.mockReturnValue(hookResult());
    rerender(<RvRatioPanel symbol="SMH" />);
    const empty = screen.getByRole("status");
    expect(empty.textContent).toContain("INSUFFICIENT HISTORY");
    expect(empty.textContent).toContain("needs at least 253 aligned closes on both legs");
  });

  it("defaults to the 1Y preset and exposes the deep 3Y/5Y presets on complete history", () => {
    render(<RvRatioPanel symbol="SMH" />);
    expect(screen.getByTestId("rv-ratio-range-chips-1y").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("rv-ratio-range-chips-3y")).toBeTruthy();
    expect(screen.getByTestId("rv-ratio-range-chips-5y")).toBeTruthy();
    fireEvent.click(screen.getByTestId("rv-ratio-range-chips-3y"));
    expect(screen.getByTestId("rv-ratio-range-chips-3y").getAttribute("aria-pressed")).toBe("true");
  });

  it("hides presets deeper than a partial payload's history", () => {
    useRvRatioMock.mockReturnValue(hookResult({ data: PARTIAL }));
    render(<RvRatioPanel symbol="SMH" />);
    expect(screen.getByTestId("rv-ratio-range-chips-1y")).toBeTruthy();
    expect(screen.queryByTestId("rv-ratio-range-chips-3y")).toBeNull();
    expect(screen.queryByTestId("rv-ratio-range-chips-5y")).toBeNull();
  });

  it("flips the chips to Custom when the brush is dragged", () => {
    render(<RvRatioPanel symbol="SMH" />);
    expect(screen.queryByTestId("rv-ratio-range-chips-custom")).toBeNull();

    const brush = screen.getByTestId("rv-ratio-brush");
    const handle = screen.getByTestId("rv-ratio-brush-handle-left");
    const brushRect = {
      x: 0, y: 0, left: 0, top: 0, right: 700, bottom: 40, width: 700, height: 40,
      toJSON: () => ({}),
    } as DOMRect;
    brush.getBoundingClientRect = () => brushRect;
    handle.getBoundingClientRect = () => brushRect;

    fireEvent.pointerDown(handle, { clientX: 700, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 0, pointerId: 1 });
    fireEvent.pointerUp(window, { clientX: 0, pointerId: 1 });

    expect(screen.getByTestId("rv-ratio-range-chips-custom")).toBeTruthy();
  });

  it("renders the sigma band, guides, parity hairline, and ringed last point", () => {
    const { container } = render(<RvRatioPanel symbol="SMH" />);
    expect(container.querySelector('[data-testid="rv-ratio-band"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="rv-ratio-band-avg"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="rv-ratio-line"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="rv-ratio-last-point"]')).toBeTruthy();
    expect(container.textContent).toContain("VOL RATIO (x SPY)");
    expect(container.textContent).toContain("Band = full-history avg +/- 1 sd.");
  });

  it("shows tooltip rows for ratio and both RV legs on hover", () => {
    const { container } = render(<RvRatioPanel symbol="SMH" />);
    const overlay = container.querySelector('[data-testid="rv-ratio-overlay"]');
    expect(overlay).toBeTruthy();
    fireEvent.mouseMove(overlay as Element, { clientX: 150, clientY: 60 });

    const tooltip = container.querySelector(".chart-tooltip");
    expect(tooltip).toBeTruthy();
    const text = tooltip?.textContent ?? "";
    expect(text).toContain("RATIO");
    expect(text).toContain("SMH RV");
    expect(text).toContain("SPY RV");
  });
});

describe("OptionsWorkspacePanel — Rel Vol tab wiring", () => {
  beforeEach(() => {
    useRvRatioMock.mockReturnValue(hookResult({ data: COMPLETE }));
    useOptionsExposureMock.mockReturnValue({ data: null, loading: true, error: null, refresh: vi.fn() });
    pushMock.mockReset();
  });

  afterEach(() => cleanup());

  it("activates Rel Vol on /options/rv-ratio and mounts the panel with the symbol", () => {
    mockPathname = "/options/rv-ratio";
    render(<OptionsWorkspacePanel symbol="SMH" />);

    const relVol = screen.getByRole("tab", { name: "Rel Vol" });
    expect(relVol.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: "Net GEX" }).getAttribute("aria-selected")).toBe("false");
    // The reserved VIX slot stays planned and disabled.
    const volatility = screen.getByRole("tab", { name: /VIX \/ Volatility/ });
    expect(volatility.getAttribute("aria-disabled")).toBe("true");
    // Panel mounted with the carried symbol.
    expect(screen.getByText("SMH / SPY")).toBeTruthy();
  });

  it("carries the selected symbol when switching tabs in both directions", () => {
    mockPathname = "/options/net-gex";
    const { unmount } = render(<OptionsWorkspacePanel symbol="SMH" />);
    fireEvent.click(screen.getByRole("tab", { name: "Rel Vol" }));
    expect(pushMock).toHaveBeenCalledWith("/options/rv-ratio?symbol=SMH");
    unmount();

    pushMock.mockReset();
    mockPathname = "/options/rv-ratio";
    render(<OptionsWorkspacePanel symbol="SMH" />);
    fireEvent.click(screen.getByRole("tab", { name: "Net GEX" }));
    expect(pushMock).toHaveBeenCalledWith("/options/net-gex?symbol=SMH");
  });

  it("keeps the shared ticker-entry form when no symbol is selected", () => {
    mockPathname = "/options/rv-ratio";
    render(<OptionsWorkspacePanel />);
    expect(screen.getByTestId("options-ticker-entry")).toBeTruthy();
  });
});
