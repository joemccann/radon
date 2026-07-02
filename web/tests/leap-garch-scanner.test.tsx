/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import LeapScanner from "../components/LeapScanner";
import GarchConvergenceScanner from "../components/GarchConvergenceScanner";
import type { GarchConvergenceData, LeapData } from "../lib/types";

afterEach(() => {
  cleanup();
});

const leapData: LeapData = {
  scan_time: "2026-07-02T14:00:00Z",
  min_gap: 5,
  results: [
    {
      ticker: "NVDA",
      price: 181.4,
      hv_20: 42.1,
      hv_60: 38.7,
      hv_252: 44.9,
      current_iv: 31.2,
      iv_rank: 12.5,
      leap_count: 8,
      best_gap: 13.7,
      is_mispriced: true,
    },
    {
      ticker: "MSFT",
      price: 490.2,
      hv_20: 18.3,
      hv_60: 19.1,
      hv_252: 21.4,
      current_iv: 20.9,
      iv_rank: 44.0,
      leap_count: 5,
      best_gap: 0.5,
      is_mispriced: false,
    },
  ],
};

const garchData: GarchConvergenceData = {
  scan_time: "2026-07-02T14:05:00Z",
  tickers: {},
  pairs: [
    {
      pair: ["NVDA", "AMD"],
      leader: "NVDA",
      lagger: "AMD",
      divergence: 2.41,
      lagger_hv_iv_gap: 9.8,
      lagger_iv_rank: 15.0,
      signal: "STRONG",
      gates_passed: true,
      failing_gates: [],
      expected_iv: 47.2,
      expected_move: 6.1,
    },
    {
      pair: ["GOOGL", "META"],
      leader: "GOOGL",
      lagger: "META",
      divergence: -0.62,
      lagger_hv_iv_gap: -1.2,
      lagger_iv_rank: null,
      signal: "NONE",
      gates_passed: false,
      failing_gates: ["Edge"],
      expected_iv: null,
      expected_move: null,
    },
  ],
};

describe("LeapScanner", () => {
  it("renders result rows with the headline gap and mispriced status", () => {
    const onScan = vi.fn();
    render(<LeapScanner data={leapData} onScan={onScan} lastSync={leapData.scan_time} />);

    const section = screen.getByTestId("leap-scanner-section");
    expect(within(section).getByText("LEAP IV Mispricing")).toBeTruthy();
    expect(within(section).getByText("NVDA")).toBeTruthy();
    expect(within(section).getByText("+13.7")).toBeTruthy();
    expect(within(section).getByText("MISPRICED")).toBeTruthy();
    expect(within(section).getByText("1 MISPRICED")).toBeTruthy();

    fireEvent.click(within(section).getByRole("button", { name: /run scan/i }));
    expect(onScan).toHaveBeenCalledTimes(1);
  });

  it("renders the empty state when no scan is on file", () => {
    render(<LeapScanner data={{ scan_time: "", min_gap: null, results: [] }} />);
    expect(screen.getByText("No LEAP scan on file")).toBeTruthy();
  });
});

describe("GarchConvergenceScanner", () => {
  it("renders pair rows with signal and gate status", () => {
    const onScan = vi.fn();
    render(<GarchConvergenceScanner data={garchData} onScan={onScan} lastSync={garchData.scan_time} />);

    const section = screen.getByTestId("garch-scanner-section");
    expect(within(section).getByText("GARCH Convergence")).toBeTruthy();
    expect(within(section).getByText("NVDA ↔ AMD")).toBeTruthy();
    expect(within(section).getByText("+2.41")).toBeTruthy();
    expect(within(section).getByText("STRONG")).toBeTruthy();
    expect(within(section).getByText("1 ACTIONABLE")).toBeTruthy();
    // Failed gates are named, never a bare dash (failing gate = the WHY).
    expect(within(section).getByText(/Edge/)).toBeTruthy();

    fireEvent.click(within(section).getByRole("button", { name: /run scan/i }));
    expect(onScan).toHaveBeenCalledTimes(1);
  });

  it("renders the empty state when no scan is on file", () => {
    render(<GarchConvergenceScanner data={{ scan_time: "", tickers: {}, pairs: [] }} />);
    expect(screen.getByText("No GARCH scan on file")).toBeTruthy();
  });
});
