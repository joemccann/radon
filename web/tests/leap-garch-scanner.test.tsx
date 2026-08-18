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
    expect(within(section).getByText("LEAP / 05")).toBeTruthy();
    expect(within(section).getByText("LEAP IV Mispricing")).toBeTruthy();
    expect(within(section).getByText("NVDA")).toBeTruthy();
    expect(within(section).getByText("+13.7")).toBeTruthy();
    expect(within(section).getByText("MISPRICED")).toBeTruthy();
    expect(within(section).getByText("1 MISPRICED")).toBeTruthy();

    fireEvent.click(within(section).getByRole("button", { name: /^scan$/i }));
    expect(onScan).toHaveBeenCalledTimes(1);
  });

  it("renders the empty state when no scan is on file", () => {
    render(<LeapScanner data={{ scan_time: "", min_gap: null, results: [] }} />);
    expect(screen.getByText("No LEAP scan on file")).toBeTruthy();
  });

  it("distinguishes a completed zero-result ticker scan from never-scanned", () => {
    render(
      <LeapScanner
        data={{
          scan_time: "2026-07-05T14:00:00Z",
          min_gap: null,
          results: [],
          universe: "explicit",
          requested_tickers: ["KO", "PEP"],
        }}
      />,
    );
    expect(screen.queryByText("No LEAP scan on file")).toBeNull();
    expect(screen.getByText("Scan complete: no qualifying setups")).toBeTruthy();
    expect(
      screen.getByText(/0 of 2 requested tickers qualified \(KO, PEP\)/),
    ).toBeTruthy();
  });

  it("submits a parsed, deduped ticker list through onTickerScan", () => {
    const onTickerScan = vi.fn();
    render(<LeapScanner data={leapData} onTickerScan={onTickerScan} />);

    fireEvent.change(screen.getByLabelText("Ticker symbols"), {
      target: { value: "nvda, amd, nvda" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    expect(onTickerScan).toHaveBeenCalledWith(["NVDA", "AMD"]);
  });

  it("rejects malformed ticker search text", () => {
    const onTickerScan = vi.fn();
    render(<LeapScanner data={leapData} onTickerScan={onTickerScan} />);

    fireEvent.change(screen.getByLabelText("Ticker symbols"), { target: { value: "MU1" } });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    expect(onTickerScan).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain(
      "Enter 1-6 letter tickers, comma-separated.",
    );
  });

  it("defaults to Best Gap descending and sorts Ticker and Status on header click", () => {
    const three: LeapData = {
      ...leapData,
      results: [
        ...leapData.results,
        {
          ticker: "AAPL",
          price: 210.1,
          hv_20: 22.0,
          hv_60: 21.0,
          hv_252: 24.0,
          current_iv: 18.0,
          iv_rank: 20.0,
          leap_count: 6,
          best_gap: 8.2,
          is_mispriced: true,
        },
      ],
    };
    render(<LeapScanner data={three} />);
    const section = screen.getByTestId("leap-scanner-section");
    const tickers = () =>
      within(section)
        .getAllByRole("link")
        .map((el) => el.textContent);

    expect(tickers()).toEqual(["NVDA", "AAPL", "MSFT"]);
    expect(within(section).getByRole("columnheader", { name: /best gap/i }).getAttribute("aria-sort")).toBe(
      "descending",
    );

    fireEvent.click(within(section).getByRole("columnheader", { name: /^ticker$/i }));
    expect(tickers()).toEqual(["AAPL", "MSFT", "NVDA"]);
    expect(within(section).getByRole("columnheader", { name: /^ticker$/i }).getAttribute("aria-sort")).toBe(
      "ascending",
    );

    fireEvent.click(within(section).getByRole("columnheader", { name: /^ticker$/i }));
    expect(tickers()).toEqual(["NVDA", "MSFT", "AAPL"]);
    expect(within(section).getByRole("columnheader", { name: /^ticker$/i }).getAttribute("aria-sort")).toBe(
      "descending",
    );

    fireEvent.click(within(section).getByRole("columnheader", { name: /^status$/i }));
    expect(tickers()[0]).toBe("MSFT");
    expect(within(section).getByRole("columnheader", { name: /^status$/i }).getAttribute("aria-sort")).toBe(
      "ascending",
    );
  });
});

describe("GarchConvergenceScanner", () => {
  it("renders pair rows with signal and gate status", () => {
    const onScan = vi.fn();
    render(<GarchConvergenceScanner data={garchData} onScan={onScan} lastSync={garchData.scan_time} />);

    const section = screen.getByTestId("garch-scanner-section");
    expect(within(section).getByText("GARCH / 06")).toBeTruthy();
    expect(within(section).getByText("GARCH Convergence")).toBeTruthy();
    // Pair cell: leader → lagger hierarchy
    expect(within(section).getByTestId("garch-row-NVDA-AMD")).toBeTruthy();
    expect(within(section).getByText("+2.41")).toBeTruthy();
    expect(within(section).getByText("STRONG")).toBeTruthy();
    expect(within(section).getByTestId("garch-actionable-count").textContent).toMatch(/1\s*ACTIONABLE/);
    // Failed gates named as chips (the WHY), not a bare dash.
    expect(within(section).getByText("Edge")).toBeTruthy();
    // Filter bar present
    expect(within(section).getByTestId("garch-filter-actionable")).toBeTruthy();

    fireEvent.click(within(section).getByRole("button", { name: /^scan$/i }));
    expect(onScan).toHaveBeenCalledTimes(1);
  });

  it("filters to actionable pairs only", () => {
    render(<GarchConvergenceScanner data={garchData} />);
    const section = screen.getByTestId("garch-scanner-section");
    fireEvent.click(within(section).getByTestId("garch-filter-actionable"));
    expect(within(section).getByTestId("garch-row-NVDA-AMD")).toBeTruthy();
    expect(within(section).queryByTestId("garch-row-GOOGL-META")).toBeNull();
  });

  it("renders the empty state when no scan is on file", () => {
    render(<GarchConvergenceScanner data={{ scan_time: "", tickers: {}, pairs: [] }} />);
    expect(screen.getByText("No GARCH scan on file")).toBeTruthy();
  });

  it("distinguishes a completed zero-result ticker scan from never-scanned", () => {
    render(
      <GarchConvergenceScanner
        data={{
          scan_time: "2026-07-05T14:00:00Z",
          tickers: {},
          pairs: [],
          universe: "explicit",
          requested_tickers: ["NVDA", "AMD"],
        }}
      />,
    );
    expect(screen.queryByText("No GARCH scan on file")).toBeNull();
    expect(screen.getByText("Scan complete: no qualifying setups")).toBeTruthy();
    expect(
      screen.getByText(/0 of 2 requested tickers qualified \(NVDA, AMD\)/),
    ).toBeTruthy();
  });

  it("submits pair tickers in order without deduping", () => {
    const onTickerScan = vi.fn();
    render(<GarchConvergenceScanner data={garchData} onTickerScan={onTickerScan} />);

    fireEvent.change(screen.getByLabelText("Ticker symbols"), {
      target: { value: "nvda, amd, nvda, tsm" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    expect(onTickerScan).toHaveBeenCalledWith(["NVDA", "AMD", "NVDA", "TSM"]);
  });

  it("rejects an odd number of pair tickers", () => {
    const onTickerScan = vi.fn();
    render(<GarchConvergenceScanner data={garchData} onTickerScan={onTickerScan} />);

    fireEvent.change(screen.getByLabelText("Ticker symbols"), {
      target: { value: "NVDA, AMD, TSM" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    expect(onTickerScan).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain(
      "Enter pairs: an even number of tickers.",
    );
  });

  it("sorts by Gates when the header is clicked", () => {
    render(<GarchConvergenceScanner data={garchData} />);
    const section = screen.getByTestId("garch-scanner-section");
    const first = () => within(section).getAllByTestId(/^garch-row-/)[0].getAttribute("data-testid");
    const before = first();
    fireEvent.click(within(section).getByRole("columnheader", { name: /gates/i }));
    expect(within(section).getByRole("columnheader", { name: /gates/i }).getAttribute("aria-sort")).toMatch(
      /^(ascending|descending)$/,
    );
    expect(first()).not.toBe(before);
  });
});
