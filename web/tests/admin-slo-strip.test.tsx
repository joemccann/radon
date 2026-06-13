/**
 * @vitest-environment jsdom
 *
 * DUR-16: the "SLO 7d" strip on /admin — three attainment tiles computed
 * from external_probe_runs. Honest rendering: a missing/empty history
 * renders "--", never a fabricated attainment.
 */
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import React from "react";

afterEach(cleanup);
import SloStrip from "../components/admin/SloStrip";
import type { ExternalProbeRunRow, SloPayload } from "../lib/adminSlo";

function run(overrides: Partial<ExternalProbeRunRow> = {}): ExternalProbeRunRow {
  return {
    run_at: "2026-06-10T15:00:00Z",
    edge_ok: 1,
    user_path_ok: 1,
    freshness_ok: 1,
    tick_fresh: 1,
    scan_fresh: 1,
    latency_ms: 250,
    ...overrides,
  };
}

function payload(rows: ExternalProbeRunRow[], missing = false): SloPayload {
  return {
    window_ms: 7 * 24 * 3_600_000,
    since: "2026-06-03T15:00:00Z",
    rows,
    ...(missing ? { missing: true } : {}),
  };
}

describe("SloStrip", () => {
  it("renders three SLO tiles with attainment vs target", () => {
    const rows = [run(), run(), run(), run({ tick_fresh: 0 })];
    render(<SloStrip slo={payload(rows)} />);

    expect(screen.getByTestId("slo-strip")).toBeTruthy();
    const edge = screen.getByTestId("tile-edge-reach-7d");
    expect(edge.textContent).toContain("100");
    expect(edge.textContent).toContain("99.5%");

    const tick = screen.getByTestId("tile-rth-ticks-7d");
    expect(tick.textContent).toContain("75");
    expect(tick.className).toContain("negative");

    const scan = screen.getByTestId("tile-scan-fresh-7d");
    expect(scan.textContent).toContain("100");
    expect(scan.className).toContain("positive");
  });

  it("renders -- for a null payload and for missing history", () => {
    const { unmount } = render(<SloStrip slo={null} />);
    expect(screen.getAllByText("--").length).toBe(3);
    unmount();

    render(<SloStrip slo={payload([], true)} />);
    expect(screen.getAllByText("--").length).toBe(3);
    expect(screen.getAllByText(/probe history pending/i).length).toBeGreaterThan(0);
  });

  it("renders -- per-SLO when its column has no applicable samples", () => {
    const rows = [run({ tick_fresh: null, scan_fresh: null })];
    render(<SloStrip slo={payload(rows)} />);
    const tick = screen.getByTestId("tile-rth-ticks-7d");
    expect(tick.textContent).toContain("--");
    const edge = screen.getByTestId("tile-edge-reach-7d");
    expect(edge.textContent).toContain("100");
  });
});
