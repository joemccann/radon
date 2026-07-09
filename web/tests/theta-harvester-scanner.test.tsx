/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ThetaHarvesterScanner from "../components/ThetaHarvesterScanner";
import type { ThetaHarvesterData } from "../lib/types";

const pushMock = vi.fn();

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: pushMock,
  }),
}));

vi.mock("@/lib/useTickerNav", () => ({
  useTickerNav: () => ({
    navigateToTicker: vi.fn(),
  }),
}));

afterEach(() => {
  cleanup();
  pushMock.mockReset();
});

function expectThetaHref(raw: string | null) {
  expect(raw).toBeTruthy();
  const url = new URL(raw!, "http://localhost");
  expect(url.pathname).toBe("/AAPL");
  expect(url.searchParams.get("deck")).toBe("c");
  expect(url.searchParams.get("expiry")).toBe("2026-07-17");
  expect(url.searchParams.get("strikes")).toBe("100");
  expect(url.searchParams.get("legs")).toBe("SELL:1x95P,SELL:1x105C");
}

const data: ThetaHarvesterData = {
  scan_time: "2026-06-24T15:00:00Z",
  source: "Unusual Whales",
  universe: "fallback:ndx100",
  tickers_scanned: 2,
  candidates_found: 1,
  theta_harvest_count: 1,
  results: [
    {
      ticker: "AAPL",
      score: 87.5,
      verdict: "THETA_HARVEST",
      spot: 100,
      iv: 35,
      hv20: 12,
      hv60: 15,
      iv_rv_edge: 23,
      iv_rv_ratio: 2.92,
      trend_20d_pct: 0.2,
      range_score: 0.86,
      dealer_support: "SUPPORT",
      net_gex: 1_500_000,
      gex_flip: 95,
      setup: "TRUE_THETA",
      gates: {
        delta_near_zero: true,
        iv_rich_vs_rv: true,
        dealer_support: true,
        theta_positive: true,
        range_bound: true,
      },
      errors: [],
      structure: {
        expiry: "20260717",
        dte: 23,
        net_delta: -0.01,
        theta: 0.075,
        gamma: -0.0042,
        vega: -0.038,
        credit: 1.9,
        short_put: {
          symbol: "AAPL260717P00095000",
          expiry: "20260717",
          strike: 95,
          right: "P",
          iv: 35,
          delta: -0.15,
          theta: -0.04,
          gamma: 0.002,
          vega: 0.018,
          bid: 0.9,
          ask: 1.1,
          volume: 200,
          open_interest: 900,
        },
        short_call: {
          symbol: "AAPL260717C00105000",
          expiry: "20260717",
          strike: 105,
          right: "C",
          iv: 35,
          delta: 0.16,
          theta: -0.035,
          gamma: 0.0022,
          vega: 0.02,
          bid: 0.8,
          ask: 1.0,
          volume: 180,
          open_interest: 850,
        },
      },
    },
  ],
};

describe("ThetaHarvesterScanner", () => {
  it("renders desktop rows, mobile cards, and the scan action", () => {
    const onScan = vi.fn();
    const onTickerScan = vi.fn();
    render(
      <ThetaHarvesterScanner
        data={data}
        onScan={onScan}
        onTickerScan={onTickerScan}
        lastSync={data.scan_time}
      />,
    );

    const section = screen.getByTestId("theta-harvester-section");
    expect(within(section).getByText("Theta Harvester")).toBeTruthy();
    expect(within(section).getAllByText("AAPL").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("TRUE THETA").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("SHORT 95P / 105C").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("+7.50/day").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("-1.0 sh").length).toBeGreaterThan(0);
    expect(within(section).getByText("+23.0 pt / 2.92x")).toBeTruthy();
    expect(within(section).getAllByText("IV RICH").length).toBeGreaterThan(0);

    const rowLink = within(section.querySelector("tbody")!).getByRole("link", {
      name: /open AAPL theta order builder/i,
    });
    fireEvent.click(rowLink);
    expect(pushMock).toHaveBeenCalledWith(expect.stringContaining("/AAPL?"));
    expectThetaHref(pushMock.mock.calls[0][0]);

    fireEvent.keyDown(rowLink, { key: "Enter" });
    expect(pushMock).toHaveBeenCalledTimes(2);
    expectThetaHref(pushMock.mock.calls[1][0]);

    const mobileList = screen.getByTestId("theta-harvester-mobile-list");
    expectThetaHref(within(mobileList).getByRole("link").getAttribute("href"));

    fireEvent.click(screen.getByRole("button", { name: /scan ndx/i }));
    expect(onScan).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText("Ticker symbol"), { target: { value: "mu" } });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));
    expect(onTickerScan).toHaveBeenCalledWith("MU");
  });

  it("passes DTE window + min-credit search params to onScan", () => {
    const onScan = vi.fn();
    render(<ThetaHarvesterScanner data={data} onScan={onScan} />);

    // Defaults are pre-filled (7 / 45 / 0).
    fireEvent.click(screen.getByRole("button", { name: /scan ndx/i }));
    expect(onScan).toHaveBeenLastCalledWith({ minDte: 7, maxDte: 45, minCredit: 0 });

    fireEvent.change(screen.getByTestId("theta-min-dte"), { target: { value: "21" } });
    fireEvent.change(screen.getByTestId("theta-max-dte"), { target: { value: "60" } });
    fireEvent.change(screen.getByTestId("theta-min-credit"), { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: /scan ndx/i }));
    expect(onScan).toHaveBeenLastCalledWith({ minDte: 21, maxDte: 60, minCredit: 1.5 });
  });

  it("clamps an inverted DTE window so max is never below min", () => {
    const onScan = vi.fn();
    render(<ThetaHarvesterScanner data={data} onScan={onScan} />);
    fireEvent.change(screen.getByTestId("theta-min-dte"), { target: { value: "40" } });
    fireEvent.change(screen.getByTestId("theta-max-dte"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: /scan ndx/i }));
    expect(onScan).toHaveBeenLastCalledWith({ minDte: 40, maxDte: 40, minCredit: 0 });
  });

  it("renders help bubbles for the theta scanner and metric inputs", () => {
    render(<ThetaHarvesterScanner data={data} />);

    const titleTrigger = screen.getByTestId("theta-harvester-title-tooltip");
    fireEvent.mouseEnter(titleTrigger);
    expect(screen.getByTestId("theta-harvester-title-tooltip-content").textContent).toContain("Neutral short-premium scan");
    fireEvent.mouseLeave(titleTrigger);

    const expectations = [
      ["score", "Composite theta harvest score"],
      ["theta", "Estimated daily theta"],
      ["net-delta", "Near zero"],
      ["iv-rv", "Implied-volatility edge"],
      ["dealer", "Dealer support gate"],
      ["range", "Range-bound score"],
      ["dte", "Days to expiration"],
      ["credit", "Estimated entry credit"],
      ["status", "Final verdict"],
    ] as const;

    for (const [key, expectedText] of expectations) {
      const trigger = screen.getByTestId(`theta-harvester-tooltip-${key}`);
      expect(trigger).toBeTruthy();
      fireEvent.mouseEnter(trigger);
      expect(screen.getByTestId(`theta-harvester-tooltip-content-${key}`).textContent).toContain(expectedText);
      fireEvent.mouseLeave(trigger);
    }
  });

  it("rejects malformed ticker search text", () => {
    const onTickerScan = vi.fn();
    render(<ThetaHarvesterScanner data={data} onTickerScan={onTickerScan} />);

    fireEvent.change(screen.getByLabelText("Ticker symbol"), { target: { value: "MU1" } });
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    expect(onTickerScan).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("Enter 1-6 letters.");
  });

  it("renders the measured-empty state when the latest scan has no candidates", () => {
    render(
      <ThetaHarvesterScanner
        data={{ ...data, theta_harvest_count: 0, candidates_found: 0, results: [] }}
      />,
    );

    expect(screen.getByText("No theta harvest candidates")).toBeTruthy();
    expect(screen.getByText("No neutral short-premium setups are measured in the latest scan.")).toBeTruthy();
  });
});
