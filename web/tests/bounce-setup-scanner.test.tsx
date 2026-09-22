/**
 * @vitest-environment jsdom
 *
 * BounceSetupScanner: docs/bounce-setup.md. The load-bearing assertion is the
 * Gate 2 wire: OPEN TRADE only exists for a BOUNCE_SETUP row that also carries
 * dark-pool flow accumulation. Everything else reads NO FLOW EDGE.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import BounceSetupScanner from "../components/BounceSetupScanner";
import { bounceOrderHref, type BounceSetupData, type BounceSetupRow } from "../lib/bounceSetup";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as Record<string, unknown>).ResizeObserver = ResizeObserverStub;

vi.mock("@/lib/useTickerNav", () => ({ useTickerNav: () => ({ navigateToTicker: vi.fn() }) }));

afterEach(() => cleanup());

function series(n = 20) {
  return Array.from({ length: n }, (_, i) => ({
    date: `2026-08-${String(21 + (i % 10)).padStart(2, "0")}`,
    spot_cum_pct: -0.3 * i,
    fs_iv_change: i < 13 ? 0.35 * i : 4.5 - 0.5 * (i - 13),
    skew30: 2.6 + 0.04 * i,
  }));
}

function row(overrides: Partial<BounceSetupRow> = {}): BounceSetupRow {
  return {
    ticker: "BAC",
    verdict: "BOUNCE_SETUP",
    stretch_rank: 1,
    stretch_pctl: 0.2,
    rsi: 24.1,
    pct_b: -0.12,
    ret_z: -2.4,
    ret_20d: -5.5,
    contract: { symbol: "BAC261016P00055000", expiry: "2026-10-16", strike: 55 },
    vol: { runup: 4.5, off_peak: 0.8, slope: -1.9, pass: true },
    skew: { ease: 0.5, slope: -0.4, pass: true },
    series: series(),
    flow: null,
    errors: [],
    ...overrides,
  };
}

function data(results: BounceSetupRow[]): BounceSetupData {
  return {
    scan_time: "2026-09-18T21:10:00Z",
    as_of: "2026-09-18",
    window: 20,
    universe: "largecaps",
    coverage: { tickers: 520, ranked: 512, excluded_short_history: 8, stage2: 30 },
    bounce_count: results.filter((r) => r.verdict === "BOUNCE_SETUP").length,
    results,
  };
}

describe("BounceSetupScanner", () => {
  it("renders the empty state on missing:true", () => {
    render(<BounceSetupScanner data={{ missing: true, scan_time: null, results: [], bounce_count: 0 } as unknown as BounceSetupData} />);
    expect(screen.getByTestId("bounce-setup-empty")).toBeTruthy();
  });

  it("renders a row with its stretch rank, verdict and contract", () => {
    render(<BounceSetupScanner data={data([row()])} />);
    const r = screen.getByTestId("bounce-row-BAC");
    expect(within(r).getByText("BOUNCE SETUP")).toBeTruthy();
    expect(within(r).getByTestId("bounce-stretch-rank").textContent).toBe("1");
    expect(within(r).getByText("BAC261016P00055000")).toBeTruthy();
    expect(within(r).getByTestId("bounce-ret20").textContent).toBe("-5.5%");
  });

  it("Gate 2: a BOUNCE_SETUP row without flow shows NO FLOW EDGE and no OPEN TRADE", () => {
    render(<BounceSetupScanner data={data([row({ flow: null })])} />);
    const r = screen.getByTestId("bounce-row-BAC");
    expect(within(r).getByText("NO FLOW EDGE")).toBeTruthy();
    expect(within(r).queryByTestId("bounce-open-trade-BAC")).toBeNull();
  });

  it("Gate 2: a distribution flow reading does not arm OPEN TRADE", () => {
    render(<BounceSetupScanner data={data([row({ flow: { signal: "DISTRIBUTION", score: 62 } })])} />);
    expect(screen.queryByTestId("bounce-open-trade-BAC")).toBeNull();
  });

  it("Gate 2: BOUNCE_SETUP plus accumulation arms OPEN TRADE with the call-spread href", () => {
    const armed = row({ flow: { signal: "ACCUMULATION", score: 71 } });
    render(<BounceSetupScanner data={data([armed])} />);
    const link = screen.getByTestId("bounce-open-trade-BAC") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe(bounceOrderHref(armed));
    expect(link.getAttribute("href")).toContain("/BAC");
  });

  it("never arms OPEN TRADE on a WATCH row, even with accumulation", () => {
    render(<BounceSetupScanner data={data([row({ verdict: "WATCH", skew: { ease: 0.1, slope: 0.2, pass: false }, flow: { signal: "ACCUMULATION", score: 80 } })])} />);
    expect(screen.queryByTestId("bounce-open-trade-BAC")).toBeNull();
  });

  it("expands a row into the spot / fixed-strike vol / inverted skew chart with no NaN", () => {
    const withGap = series().map((p, i) => (i === 5 ? { ...p, skew30: null } : p));
    const { container } = render(<BounceSetupScanner data={data([row({ series: withGap as BounceSetupRow["series"] })])} />);
    fireEvent.click(screen.getByTestId("bounce-row-BAC"));
    expect(screen.getByTestId("bounce-detail-chart-BAC")).toBeTruthy();
    const paths = Array.from(container.querySelectorAll("path[d]"));
    expect(paths.length).toBeGreaterThan(0);
    for (const p of paths) expect(p.getAttribute("d")).not.toContain("NaN");
  });

  it("uses descriptive copy only", () => {
    const { container } = render(<BounceSetupScanner data={data([row()])} />);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/looks like|expected return|compass/i);
    expect(text).not.toContain("—");
  });
});
