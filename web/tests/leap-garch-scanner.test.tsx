/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import LeapScanner, { leapOrderHref } from "../components/LeapScanner";
import GarchConvergenceScanner, { garchOrderHref } from "../components/GarchConvergenceScanner";
import type { GarchConvergenceData, LeapData } from "../lib/types";

afterEach(() => {
  cleanup();
});

const leapData: LeapData = {
  // Fresh: the TRADE BEST link is suppressed past the leap-scan freshness
  // window, and these cases are about RANKING, not staleness. R-415.
  scan_time: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
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
      best_leap: {
        symbol: "NVDA270115C00210000",
        expiry: "2027-01-15",
        strike: 210,
        right: "C",
        iv: 28.4,
        delta: 0.42,
        gap: 13.7,
        oi: 900,
        volume: 12,
      },
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
  scan_time: new Date(Date.now() - 25 * 60 * 1000).toISOString(),
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
    {
      pair: ["TSM", "ASML"],
      leader: "",
      lagger: "",
      divergence: 0,
      lagger_hv_iv_gap: 0,
      lagger_iv_rank: null,
      signal: "",
      gates_passed: false,
      failing_gates: ["MISSING_DATA"],
      expected_iv: null,
      expected_move: null,
    },
  ],
};

describe("leapOrderHref", () => {
  it("deep-links the contract into the chain order builder", () => {
    expect(leapOrderHref(leapData.results[0])).toBe(
      "/NVDA?deck=c&expiry=2027-01-15&strikes=100&legs=BUY%3A1x210C&src=leap",
    );
  });

  it("keeps fractional strikes intact", () => {
    expect(
      leapOrderHref({
        ...leapData.results[0],
        ticker: "spy",
        best_leap: { ...leapData.results[0].best_leap!, strike: 612.5, right: "P" },
      }),
    ).toBe("/SPY?deck=c&expiry=2027-01-15&strikes=100&legs=BUY%3A1x612.5P&src=leap");
  });

  it("has no destination for a row without a contract", () => {
    expect(leapOrderHref(leapData.results[1])).toBeNull();
  });
});

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

  it("links the widest-gap mispriced row into the options order entry view", () => {
    render(<LeapScanner data={leapData} />);

    const section = screen.getByTestId("leap-scanner-section");
    const best = within(section).getByTestId("leap-best-order-link");
    expect(best.getAttribute("href")).toBe(
      "/NVDA?deck=c&expiry=2027-01-15&strikes=100&legs=BUY%3A1x210C&src=leap",
    );
    expect(best.textContent).toContain("NVDA 210C");

    const row = within(section).getByTestId("leap-order-link-NVDA");
    expect(row.getAttribute("href")).toBe(
      "/NVDA?deck=c&expiry=2027-01-15&strikes=100&legs=BUY%3A1x210C&src=leap",
    );
    expect(within(section).queryByTestId("leap-order-link-MSFT")).toBeNull();
  });

  it("picks the widest gap, not the first mispriced row", () => {
    // R-388: ranking now reads the LINKED CONTRACT's own gap, not the group's
    // `best_gap`, so the fixture raises both. `best_gap` alone described the
    // delta bucket rather than the contract the button opens, which is how a
    // ticker whose contract was worth 18 vol points lost the headline slot to
    // one whose contract was worth 6.
    const wider = {
      ...leapData,
      results: [
        leapData.results[0],
        {
          ...leapData.results[0],
          ticker: "CRM",
          best_gap: 44.8,
          best_leap: {
            ...leapData.results[0].best_leap!,
            strike: 260,
            expiry: "2027-06-17",
            gap: 44.8,
          },
        },
      ],
    };
    render(<LeapScanner data={wider} />);
    expect(
      screen.getByTestId("leap-best-order-link").getAttribute("href"),
    ).toBe("/CRM?deck=c&expiry=2027-06-17&strikes=100&legs=BUY%3A1x260C&src=leap");
  });

  it("omits the order action when the scan predates contract detail", () => {
    const legacy = {
      ...leapData,
      results: leapData.results.map(({ best_leap: _ignored, ...rest }) => rest),
    };
    render(<LeapScanner data={legacy} />);
    const section = screen.getByTestId("leap-scanner-section");
    expect(within(section).queryByTestId("leap-best-order-link")).toBeNull();
    expect(within(section).queryByTestId("leap-order-link-NVDA")).toBeNull();
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
      Array.from(section.querySelectorAll("tbody tr td:first-child a")).map((el) => el.textContent);

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

describe("garchOrderHref", () => {
  it("deep-links the lagger into the chain deck", () => {
    expect(garchOrderHref(garchData.pairs[0])).toBe("/AMD?deck=c&src=garch");
  });

  it("uppercases and encodes the lagger", () => {
    expect(garchOrderHref({ ...garchData.pairs[0], lagger: "brk.b" })).toBe(
      "/BRK.B?deck=c&src=garch",
    );
  });

  it("has no destination for a row that fails its gates", () => {
    expect(garchOrderHref(garchData.pairs[1])).toBeNull();
  });

  it("has no destination for a row with no established lagger", () => {
    expect(garchOrderHref(garchData.pairs[2])).toBeNull();
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

  it("links an actionable lagger into the chain deck and leaves failed rows alone", () => {
    render(<GarchConvergenceScanner data={garchData} />);
    const section = screen.getByTestId("garch-scanner-section");

    const link = within(section).getByTestId("garch-order-link-AMD");
    expect(link.getAttribute("href")).toBe("/AMD?deck=c&src=garch");
    expect(link.textContent).toBe("AMD");

    expect(within(section).queryByTestId("garch-order-link-META")).toBeNull();
    const failed = within(section).getByTestId("garch-row-GOOGL-META");
    const failedLinks = Array.from(failed.querySelectorAll("a"));
    expect(failedLinks.map((a) => a.getAttribute("href"))).toEqual(["/META"]);
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
