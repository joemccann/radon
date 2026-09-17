/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import VolSkewMrScanner, { volSkewMrOrderHref } from "../components/VolSkewMrScanner";
import type { VolSkewMrData, VolSkewMrResult } from "../lib/types";

vi.mock("@/lib/useTickerNav", () => ({
  useTickerNav: () => ({
    navigateToTicker: vi.fn(),
  }),
}));

afterEach(() => {
  cleanup();
});

const data: VolSkewMrData = {
  scan_time: "2026-09-16T15:00:00Z",
  source: "Unusual Whales + Radon vol/skew feeds",
  universe: "fallback:ndx100",
  tickers_scanned: 3,
  candidates_found: 3,
  actionable_count: 2,
  results: [
    {
      ticker: "AAPL",
      verdict: "TOP_MR",
      spot: 212.4,
      rsi: 78,
      pct_b: 1.04,
      extension: "HIGH",
      iv_path: "falling",
      skew_path: "falling",
      suggested_structure: "put spread",
      gates: { technicals: true, iv: true, skew: true },
      errors: [],
    },
    {
      ticker: "INTC",
      verdict: "BOTTOM_MR",
      spot: 22.1,
      rsi: 24,
      pct_b: -0.05,
      extension: "LOW",
      iv_path: "flat",
      skew_path: "falling",
      suggested_structure: "call spread",
      gates: { technicals: true, iv: true, skew: true },
      errors: [],
    },
    {
      ticker: "NVDA",
      verdict: "BREAKOUT",
      spot: 181.4,
      rsi: 74,
      pct_b: 1.1,
      extension: "HIGH",
      iv_path: "rising",
      skew_path: "rising",
      suggested_structure: null,
      gates: { technicals: true, iv: true, skew: false },
      errors: [],
    },
  ],
};

describe("VolSkewMrScanner", () => {
  it("renders AAPL-class top, bottom, and continue labels", () => {
    render(<VolSkewMrScanner data={data} />);
    expect(screen.getByTestId("vol-skew-mr-section")).toBeTruthy();
    expect(screen.getAllByText("TOP MR").length).toBeGreaterThan(0);
    expect(screen.getAllByText("BOTTOM MR").length).toBeGreaterThan(0);
    expect(screen.getAllByText("BREAKOUT").length).toBeGreaterThan(0);
    expect(screen.getAllByText("put spread").length).toBeGreaterThan(0);
    expect(screen.getAllByText("call spread").length).toBeGreaterThan(0);
    expect(screen.getAllByText("continue").length).toBeGreaterThan(0);
  });

  it("credits Options Insight in the help tooltip", () => {
    render(<VolSkewMrScanner data={data} />);
    fireEvent.mouseEnter(screen.getByTestId("vol-skew-mr-title-tooltip"));
    expect(screen.getByTestId("vol-skew-mr-title-tooltip-content").textContent).toContain("Options Insight");
    expect(screen.getByTestId("vol-skew-mr-title-tooltip-content").textContent).toContain("Imran Lakha");
  });

  it("fires comma ticker search through onTickerScan", () => {
    const onTickerScan = vi.fn();
    render(<VolSkewMrScanner data={data} onTickerScan={onTickerScan} />);
    fireEvent.change(screen.getByLabelText("Ticker symbols"), { target: { value: "AAPL, MSFT" } });
    fireEvent.submit(screen.getByLabelText("Ticker symbols").closest("form") as HTMLFormElement);
    expect(onTickerScan).toHaveBeenCalledWith(["AAPL", "MSFT"]);
  });

  it("labels a NO_SIGNAL row as no structure rather than continue", () => {
    const quiet: VolSkewMrData = {
      ...data,
      results: [{ ...data.results[0], ticker: "IBM", verdict: "NO_SIGNAL", suggested_structure: null }],
    };
    render(<VolSkewMrScanner data={quiet} />);
    expect(screen.queryByText("continue")).toBeNull();
  });

  it("renders every row without a gate map payload", () => {
    const partial = {
      ...data,
      results: [{ ...data.results[0], gates: undefined as unknown as VolSkewMrResult["gates"] }],
    };
    render(<VolSkewMrScanner data={partial} />);
    expect(screen.getByTestId("vol-skew-mr-row-AAPL")).toBeTruthy();
  });

  it("links spot into the chain deck for actionable rows only", () => {
    render(<VolSkewMrScanner data={data} />);
    const link = screen.getByTestId("vol-skew-mr-order-link-AAPL") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/AAPL?deck=c&src=vol-skew-mr");
    expect(link.textContent).toBe("$212.40");
    cleanup();
    const quiet: VolSkewMrData = {
      ...data,
      results: [{ ...data.results[0], verdict: "NO_SIGNAL", suggested_structure: null }],
    };
    render(<VolSkewMrScanner data={quiet} />);
    expect(screen.queryByTestId("vol-skew-mr-order-link-AAPL")).toBeNull();
    expect(screen.getAllByText("$212.40").length).toBeGreaterThan(0);
  });

  it("opens a chain href for MR rows and skips NO_SIGNAL", () => {
    const quiet: VolSkewMrResult = {
      ...data.results[0],
      ticker: "IBM",
      verdict: "NO_SIGNAL",
    };
    expect(volSkewMrOrderHref(data.results[0])).toBe("/AAPL?deck=c&src=vol-skew-mr");
    expect(volSkewMrOrderHref(quiet)).toBeNull();
  });
});

 it("distinguishes missing skew history from a measured direction", () => {
  render(<VolSkewMrScanner data={{ ...data, results: [{ ...data.results[0], skew_path: "unknown", gates: { technicals: true, iv: true, skew: false } }] }} />);
  expect(screen.getByRole("status").textContent).toContain("Skew history unavailable for 1 of 1 names");
  expect(screen.getAllByText(/Insufficient history/).length).toBeGreaterThan(0);
 });

describe("safe scan failures", () => {
  it.each([
    JSON.stringify({ scan_succeeded: false, error: "Radon API 502: Subprocess capacity exhausted", results: [] }),
    "<html><body>502 Bad Gateway nginx</body></html>",
    "Traceback: internal service failure /srv/radon/scanner.py",
  ])("keeps prior observations and renders a safe retry banner", (error) => {
    const retry = vi.fn();
    render(<VolSkewMrScanner data={data} error={error} onRetry={retry} />);
    expect(screen.getByRole("alert").textContent).not.toContain(error);
    expect(screen.getByRole("alert").textContent).not.toMatch(/Subprocess|Traceback|nginx|scan_succeeded|\/srv/);
    expect(screen.getByTestId("vol-skew-mr-row-AAPL")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /retry|try again/i }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("does not imply an empty successful scan when the initial request failed", () => {
    render(<VolSkewMrScanner data={null} error="Radon API 502: Subprocess capacity exhausted" />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.queryByText("No vol/skew MR readings")).toBeNull();
  });
});
