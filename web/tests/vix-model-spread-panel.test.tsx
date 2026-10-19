/** @vitest-environment jsdom */
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { VixModelSpreadEntry } from "@/lib/vixModelSpread";
const state = vi.hoisted(() => ({ hook: vi.fn(), mobile: false }));
vi.mock("@/lib/useVixModelSpread", () => ({ useVixModelSpread: state.hook }));
vi.mock("@/lib/useViewport", () => ({ useViewport: () => ({ isMobile: state.mobile, hasMounted: true }) }));
vi.mock("@/components/RequestError", () => ({ default: ({ error }: { error: unknown }) => error ? <div data-testid="error-toast">Refresh failed</div> : null }));
import VixModelSpreadPanel from "@/components/VixModelSpreadPanel";
import VixModelSpreadChart from "@/components/VixModelSpreadChart";

function entry(i = 0): VixModelSpreadEntry {
  return { date: new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10), vix: 22, vixeq: 40, cor1m: 25, model_vix: 20, spread: 2,
    mean_20: 1, std_20: 0.25, upper_20: 1.5, lower_20: 0.5, z_20: 4,
    mean_252: 2, std_252: 0.5, upper_252: 3, lower_252: 1, z_252: 0 };
}
function data(length = 300) {
  const series = Array.from({ length }, (_, i) => entry(i));
  return { series, count: length, current: series.at(-1), data_date: "2026-10-16", as_of: "2026-10-16", scan_time: "2026-10-17T02:50:00Z", status: "ok", source: "cboe" };
}
function setup(payload = data()) {
  state.hook.mockReturnValue({ data: payload, loading: false, syncing: false, error: null, syncNow: vi.fn() });
}
beforeAll(() => { globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver; });
beforeEach(() => { state.mobile = false; setup(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("VIX model panel", () => {
  it("shows source loading", () => { state.hook.mockReturnValue({ data: null, loading: true, syncing: false }); render(<VixModelSpreadPanel />); expect(screen.getByText("Loading VIX model spread")).toBeTruthy(); });
  it("explains missing aligned observations", () => { state.hook.mockReturnValue({ data: { missing: true }, loading: false, syncing: false }); render(<VixModelSpreadPanel />); expect(screen.getByText("No aligned VIX model data")).toBeTruthy(); });
  it("defaults to 20-session statistics and one year, changes bands and state together", () => {
    render(<VixModelSpreadPanel />);
    expect(screen.getByTestId("vix-model-spread-hero").textContent).toBe("2.00 pts");
    expect(screen.getByTestId("vix-model-spread-state").textContent).toBe("ABOVE BAND");
    expect(screen.getByTestId("vix-model-spread-range-chips-1y").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getAllByTestId("vix-model-spread-bar")).toHaveLength(252);
    const path = screen.getByTestId("vix-model-spread-upper-path").getAttribute("d");
    fireEvent.click(screen.getByRole("button", { name: "252 sessions" }));
    expect(screen.getByTestId("vix-model-spread-state").textContent).toBe("WITHIN BAND");
    expect(screen.getByTestId("vix-model-spread-mean").textContent).toContain("2.00");
    expect(screen.getByTestId("vix-model-spread-z").textContent).toContain("0.00");
    expect(screen.getByTestId("vix-model-spread-upper-path").getAttribute("d")).not.toBe(path);
  });
  it("range selection changes bars without changing full-history statistics", () => {
    render(<VixModelSpreadPanel />);
    fireEvent.click(screen.getByTestId("vix-model-spread-range-chips-1m"));
    expect(screen.getAllByTestId("vix-model-spread-bar")).toHaveLength(21);
    expect(screen.getByTestId("vix-model-spread-state").textContent).toBe("ABOVE BAND");
    expect(screen.getByTestId("vix-model-spread-mean").textContent).toContain("1.00");
    expect(screen.getByTestId("vix-model-spread-brush")).toBeTruthy();
  });
  it("uses data_date and the exact 02:50 UTC schedule", () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-17T01:20:00Z"));
    render(<VixModelSpreadPanel />);
    expect(screen.getByTestId("vix-model-spread-strip-asof").textContent).toBe("2026-10-16");
    expect(screen.getByTestId("vix-model-spread-freshness-rail-countdown").textContent).toBe("1h 30m");
  });
  it("keeps all four mobile metrics and one date owner", () => {
    state.mobile = true; render(<VixModelSpreadPanel />);
    expect(screen.getByTestId("vix-model-spread-mobile-grid").children).toHaveLength(4);
    expect(screen.getAllByTestId("vix-model-spread-strip-asof")).toHaveLength(1);
  });
  it("retains measured data when refresh reports an error", () => {
    state.hook.mockReturnValue({ data: data(), error: "network", loading: false, syncing: false });
    render(<VixModelSpreadPanel />); expect(screen.getByTestId("error-toast")).toBeTruthy(); expect(screen.getByTestId("vix-model-spread-hero")).toBeTruthy();
  });
  it("uses all for short samples and omits a one-point brush", () => {
    setup(data(1)); render(<VixModelSpreadPanel />);
    expect(screen.queryByTestId("vix-model-spread-brush")).toBeNull();
    expect(screen.getAllByTestId("vix-model-spread-bar")).toHaveLength(1);
  });
  it.each([
    ["BELOW BAND", { spread: -1 }],
    ["WITHIN BAND", { spread: 1.5 }],
    ["WARMUP", { mean_20: null }],
    ["NO VARIATION", { std_20: 0 }],
  ])("renders %s from the selected statistics", (expected, overrides) => {
    const payload = data(2);
    payload.current = { ...payload.current!, ...overrides };
    setup(payload); render(<VixModelSpreadPanel />);
    expect(screen.getByTestId("vix-model-spread-state").textContent).toBe(expected);
  });
  it("states historical provenance without a convergence promise", () => {
    render(<VixModelSpreadPanel />);
    expect(screen.getByTestId("vix-model-spread-source-footnote").textContent).toContain("November 4, 2024 launch is backtested");
  });
});

describe("VIX model chart", () => {
  it("paints signed spreads on opposite sides of zero", () => {
    render(<VixModelSpreadChart history={[entry(), { ...entry(1), spread: -2 }]} window={20} />);
    const bars = screen.getAllByTestId("vix-model-spread-bar");
    expect(bars[0].getAttribute("fill")).toBe("var(--negative)");
    expect(bars[1].getAttribute("fill")).toBe("var(--positive)");
    const zero = Number(screen.getByTestId("vix-model-spread-zero").getAttribute("y1"));
    expect(Number(bars[0].getAttribute("y"))).toBeLessThan(zero);
    expect(Number(bars[1].getAttribute("y"))).toBe(zero);
  });
  it("filters nonfinite core data and breaks paths at missing bands", () => {
    const rows = [entry(), { ...entry(1), mean_20: null, upper_20: null, lower_20: null }, entry(2), { ...entry(3), spread: NaN }];
    const { container } = render(<VixModelSpreadChart history={rows} window={20} />);
    expect(screen.getAllByTestId("vix-model-spread-bar")).toHaveLength(3);
    expect(screen.getByTestId("vix-model-spread-mean-path").getAttribute("d")?.match(/M/g)).toHaveLength(2);
    expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
  });
  it("shows a measured empty chart without invalid SVG", () => {
    render(<VixModelSpreadChart history={[{ ...entry(), spread: Infinity }]} window={20} />);
    expect(screen.getByTestId("vix-model-spread-chart-empty")).toBeTruthy(); expect(screen.queryByRole("img")).toBeNull();
  });
  it("shows the actual full date and selected-window hover values", () => {
    render(<VixModelSpreadChart history={[entry(), entry(1)]} window={252} />);
    const target = screen.getByTestId("vix-model-spread-hover");
    vi.spyOn(target, "getBoundingClientRect").mockReturnValue({ left: 0, width: 658, top: 0 } as DOMRect);
    fireEvent.mouseMove(target, { clientX: 1, clientY: 10 });
    const tooltip = screen.getByTestId("vix-model-spread-tooltip");
    expect(tooltip.textContent).toContain("01 Jan 2026"); expect(tooltip.textContent).toContain("Mean (252 sessions)"); expect(tooltip.textContent).toContain("Model VIX20.00");
    fireEvent.mouseLeave(target); expect(screen.queryByTestId("vix-model-spread-tooltip")).toBeNull();
  });
});
