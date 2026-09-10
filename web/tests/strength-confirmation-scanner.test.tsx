/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import StrengthConfirmationScanner, { strengthOrderHref } from "../components/StrengthConfirmationScanner";
import type {
  StrengthConfirmationData,
  StrengthConfirmationResult,
  StrengthFactorAssessment,
} from "../lib/types";

vi.mock("@/lib/useTickerNav", () => ({
  useTickerNav: () => ({
    navigateToTicker: vi.fn(),
  }),
}));

afterEach(() => {
  cleanup();
});

const groups = [
  "Q-SCORES",
  "NET GEX",
  "CALL POSITIONING",
  "TERM STRUCTURE",
  "VOLATILITY SMILE",
  "SYSTEMATIC POSITIONING",
  "MARKET BREADTH",
];

function factor(group: string, passed = true, source: "UW" | "APPROX" = "UW"): StrengthFactorAssessment {
  return {
    group,
    passed,
    checks_passed: passed ? 3 : 2,
    checks_total: 3,
    source,
    notes: source === "APPROX" ? ["Approximation"] : [],
    checks: [
      { label: `${group} check`, passed, value: passed ? 1 : 0, threshold: "pass", note: "Measured", source },
    ],
  };
}

const data: StrengthConfirmationData = {
  scan_time: "2026-06-24T15:00:00Z",
  source: "Unusual Whales + Radon regime caches",
  universe: "fallback:ndx100",
  tickers_scanned: 2,
  candidates_found: 2,
  confirmed_strength_count: 1,
  results: [
    {
      ticker: "AAPL",
      verdict: "REAL_STRENGTH_CONFIRMED",
      score: 100,
      groups_passed: 7,
      spot: 212.4,
      factors: groups.map((group) => factor(group, true, group === "SYSTEMATIC POSITIONING" ? "APPROX" : "UW")),
      errors: [],
    },
    {
      ticker: "MSFT",
      verdict: "WATCHLIST",
      score: 86,
      groups_passed: 6,
      spot: 486.1,
      factors: groups.map((group) => factor(group, group !== "TERM STRUCTURE", group === "SYSTEMATIC POSITIONING" ? "APPROX" : "UW")),
      errors: [],
    },
  ],
};

const weakRow: StrengthConfirmationResult = {
  ticker: "INTC",
  verdict: "WEAK",
  score: 41,
  groups_passed: 3,
  spot: 33.7,
  factors: groups.map((group) => factor(group, group === "Q-SCORES" || group === "NET GEX" || group === "MARKET BREADTH")),
  errors: [],
};

const dataWithWeak: StrengthConfirmationData = {
  ...data,
  tickers_scanned: 3,
  candidates_found: 3,
  results: [...data.results, weakRow],
};

describe("strengthOrderHref", () => {
  it("deep-links the ticker into the chain deck with no invented contract", () => {
    expect(strengthOrderHref(data.results[0])).toBe("/AAPL?deck=c&src=strength");
    expect(strengthOrderHref(data.results[1])).toBe("/MSFT?deck=c&src=strength");
  });

  it("uppercases and encodes the ticker", () => {
    expect(strengthOrderHref({ ...data.results[0], ticker: "brk.b" })).toBe(
      "/BRK.B?deck=c&src=strength",
    );
  });

  it("has no destination for a WEAK row", () => {
    expect(strengthOrderHref(weakRow)).toBeNull();
  });

  it("has no destination for a row without a ticker", () => {
    expect(strengthOrderHref({ ...data.results[0], ticker: "   " })).toBeNull();
  });
});

describe("StrengthConfirmationScanner", () => {
  it("renders desktop factor rows, mobile cards, and scan actions", () => {
    const onScan = vi.fn();
    const onTickerScan = vi.fn();
    render(
      <StrengthConfirmationScanner
        data={data}
        onScan={onScan}
        onTickerScan={onTickerScan}
        lastSync={data.scan_time}
      />,
    );

    const section = screen.getByTestId("strength-confirmation-section");
    expect(within(section).getByText("7-Step Strength")).toBeTruthy();
    expect(within(section).getByText("1 CONFIRMED")).toBeTruthy();
    expect(within(section).getAllByText("AAPL").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("REAL STRENGTH").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("100").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("TERM").length).toBeGreaterThan(0);
    expect(within(section).getByText("FAILED: NONE")).toBeTruthy();
    expect(within(section).getAllByText("TERM").some((node) => node.closest(".strength-factor-chip"))).toBe(true);

    const mobileList = screen.getByTestId("strength-confirmation-mobile-list");
    expect(within(mobileList).getAllByText("AAPL").length).toBeGreaterThan(0);
    expect(within(mobileList).getAllByText("CONFIRMATION SCORE").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /scan ndx/i }));
    expect(onScan).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText("Strength ticker symbol"), { target: { value: "mu" } });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));
    expect(onTickerScan).toHaveBeenCalledWith("MU");
  });

  it("renders help bubbles for all seven strength inputs", () => {
    render(<StrengthConfirmationScanner data={data} />);

    const expectations = [
      ["q", "Composite sentiment gate"],
      ["gex", "Dealer gamma gate"],
      ["call", "Upside positioning gate"],
      ["term", "Vol curve gate"],
      ["smile", "Skew gate"],
      ["sys", "Systematic flow gate"],
      ["breadth", "Breadth gate"],
    ] as const;

    for (const [key, expectedText] of expectations) {
      const trigger = screen.getByTestId(`strength-factor-tooltip-${key}`);
      expect(trigger).toBeTruthy();
      fireEvent.mouseEnter(trigger);
      expect(screen.getByTestId(`strength-factor-tooltip-content-${key}`).textContent).toContain(expectedText);
      fireEvent.mouseLeave(trigger);
    }
  });

  it("rejects malformed ticker search text", () => {
    const onTickerScan = vi.fn();
    render(<StrengthConfirmationScanner data={data} onTickerScan={onTickerScan} />);

    fireEvent.change(screen.getByLabelText("Strength ticker symbol"), { target: { value: "MU1" } });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    expect(onTickerScan).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("Enter 1-6 letters.");
  });

  it("renders the measured-empty state when no names pass", () => {
    render(
      <StrengthConfirmationScanner
        data={{ ...data, confirmed_strength_count: 0, candidates_found: 0, results: [] }}
      />,
    );

    expect(screen.getByText("No confirmed strength setups")).toBeTruthy();
    expect(screen.getByText("The latest scan did not find names with all seven factor groups aligned.")).toBeTruthy();
  });

  it("sorts by Failed when the header is clicked", () => {
    render(<StrengthConfirmationScanner data={data} />);
    const section = screen.getByTestId("strength-confirmation-section");
    const firstTicker = () =>
      within(section).getAllByRole("row")[1].querySelector(".ticker-link")?.textContent;
    expect(firstTicker()).toBe("AAPL");
    fireEvent.click(within(section).getByRole("columnheader", { name: /^failed$/i }));
    fireEvent.click(within(section).getByRole("columnheader", { name: /^failed$/i }));
    expect(within(section).getByRole("columnheader", { name: /^failed$/i }).getAttribute("aria-sort")).toBe(
      "descending",
    );
    expect(firstTicker()).toBe("MSFT");
  });

  it("links actionable rows into the chain order builder on desktop and mobile", () => {
    render(<StrengthConfirmationScanner data={dataWithWeak} />);

    const links = screen.getAllByTestId("strength-order-link-AAPL");
    expect(links.length).toBe(2);
    for (const link of links) {
      expect(link.getAttribute("href")).toBe("/AAPL?deck=c&src=strength");
    }

    const watchlist = screen.getAllByTestId("strength-order-link-MSFT");
    expect(watchlist.length).toBe(2);
    for (const link of watchlist) {
      expect(link.getAttribute("href")).toBe("/MSFT?deck=c&src=strength");
    }

    expect(screen.queryAllByTestId("strength-order-link-INTC").length).toBe(0);
    expect(screen.getAllByText("INTC").length).toBeGreaterThan(0);
  });

  it("renders no order link when the row cannot address a ticker", () => {
    render(
      <StrengthConfirmationScanner
        data={{ ...data, results: [{ ...data.results[0], ticker: "   " }] }}
      />,
    );

    expect(screen.queryAllByRole("link").length).toBe(0);
  });
});
