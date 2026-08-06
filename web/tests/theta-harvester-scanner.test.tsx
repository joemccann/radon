/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ThetaHarvesterScanner, { formatThetaEarningsLabel } from "../components/ThetaHarvesterScanner";
import type { ThetaHarvesterData, ThetaHarvesterEarnings, ThetaHarvesterResult } from "../lib/types";

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
    expect(within(section).getByText("THETA / 03")).toBeTruthy();
    expect(within(section).getByText("Theta Harvester")).toBeTruthy();
    expect(within(section).getAllByText("AAPL").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("SHORT 95P / 105C").length).toBeGreaterThan(0);
    // Mobile card keeps the /day label; the desktop grid uses the compact /d.
    expect(within(section).getAllByText("+7.50/day").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("+7.50/d").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("-1.0 sh").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("2.92×").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("+23.0 pt").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("IV RICH").length).toBeGreaterThan(0);

    // The ticker link goes straight to the order builder (e2e contract).
    const grid = screen.getByTestId("theta-grid");
    const tickerLink = within(grid).getByRole("link", {
      name: /open AAPL theta order builder/i,
    });
    expectThetaHref(tickerLink.getAttribute("href"));

    // Row click expands the reconstructed position; Send to orders navigates.
    fireEvent.click(within(grid).getByRole("button", { name: "AAPL details" }));
    const detail = screen.getByTestId("theta-grid-detail-AAPL");
    expect(within(detail).getByText("SELL 95 PUT")).toBeTruthy();
    expect(within(detail).getByText("SELL 105 CALL")).toBeTruthy();
    expect(within(detail).getByText("2.92× · +23.0 pt")).toBeTruthy();
    expect(within(detail).getAllByText("TRUE THETA").length).toBeGreaterThan(0);
    fireEvent.click(within(detail).getByRole("button", { name: "Send to orders" }));
    expect(pushMock).toHaveBeenCalledWith(expect.stringContaining("/AAPL?"));
    expectThetaHref(pushMock.mock.calls[0][0]);

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

    // Live column headers carry their help bubbles unexpanded…
    const headerExpectations = [
      ["score", "Composite theta harvest score"],
      ["theta", "Estimated daily theta"],
      ["iv-rv", "Implied-volatility edge"],
      ["credit", "Estimated entry credit"],
      ["dte", "Days to expiration"],
    ] as const;
    // …the demoted columns keep theirs inside the expanded detail.
    const demotedExpectations = [
      ["net-delta", "Near zero"],
      ["dealer", "Dealer support gate"],
      ["range", "Range-bound score"],
      ["earnings", "Next earnings date if it falls inside"],
      ["status", "Final verdict"],
    ] as const;

    const probe = (key: string, expectedText: string) => {
      const trigger = screen.getByTestId(`theta-harvester-tooltip-${key}`);
      expect(trigger).toBeTruthy();
      fireEvent.mouseEnter(trigger);
      expect(screen.getByTestId(`theta-harvester-tooltip-content-${key}`).textContent).toContain(expectedText);
      fireEvent.mouseLeave(trigger);
    };

    for (const [key, expectedText] of headerExpectations) probe(key, expectedText);

    fireEvent.click(screen.getByRole("button", { name: "AAPL details" }));
    for (const [key, expectedText] of demotedExpectations) probe(key, expectedText);
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

  it("formats earnings labels for null, same-day, and future sessions", () => {
    expect(formatThetaEarningsLabel(null)).toBe("---");
    expect(formatThetaEarningsLabel(undefined)).toBe("---");
    expect(formatThetaEarningsLabel({
      report_date: "2026-08-05",
      report_time: "postmarket",
      days_until: 0,
      within_dte: true,
    })).toBe("TODAY · AMC");
    expect(formatThetaEarningsLabel({
      report_date: "2026-08-05",
      report_time: "premarket",
      days_until: 0,
      within_dte: true,
    })).toBe("TODAY · BMO");
    expect(formatThetaEarningsLabel({
      report_date: "2026-08-05",
      report_time: "postmarket",
      days_until: 3,
      within_dte: true,
    })).toBe("Aug 5 · AMC");
    expect(formatThetaEarningsLabel({
      report_date: "2026-11-04",
      report_time: "premarket",
      days_until: 91,
      within_dte: false,
    })).toBe("Nov 4 · BMO");
    expect(formatThetaEarningsLabel({
      report_date: "2026-09-12",
      report_time: "unknown",
      days_until: 10,
      within_dte: true,
    })).toBe("Sep 12 · TBD");
  });

  it("renders earnings chips: hot today, quiet outside DTE, empty when missing", () => {
    const withinDte: ThetaHarvesterEarnings = {
      report_date: "2026-08-05",
      report_time: "postmarket",
      days_until: 0,
      within_dte: true,
      source: "company",
      expected_move_pct: 8.76,
    };
    const outsideDte: ThetaHarvesterEarnings = {
      report_date: "2026-11-04",
      report_time: "premarket",
      days_until: 91,
      within_dte: false,
    };
    const multi: ThetaHarvesterData = {
      ...data,
      tickers_scanned: 3,
      candidates_found: 3,
      results: [
        { ...data.results[0], ticker: "HONA", earnings: withinDte },
        { ...data.results[0], ticker: "MSFT", earnings: outsideDte },
        { ...data.results[0], ticker: "ZZZZ", earnings: null },
      ],
    };

    render(<ThetaHarvesterScanner data={multi} />);

    // Collapsed: earnings cells live on the mobile cards only; the desktop
    // grid surfaces within-DTE risk as an ER badge on the row.
    const cells = screen.getAllByTestId("theta-earnings-cell");
    expect(cells.length).toBe(3);

    const todayAmc = cells.filter((el) => el.className.includes("theta-earnings--hot"));
    expect(todayAmc.length).toBe(1);
    for (const el of todayAmc) {
      expect(el.textContent ?? "").toMatch(/TODAY/);
      expect(el.textContent ?? "").toMatch(/AMC/);
      expect(el.getAttribute("data-within-dte")).toBe("true");
      expect(el.getAttribute("title") ?? "").toContain("after close");
      expect(el.getAttribute("title") ?? "").toContain("8.8%");
    }

    const novBmo = cells.filter((el) => el.className.includes("theta-earnings--quiet"));
    expect(novBmo.length).toBe(1);
    for (const el of novBmo) {
      expect(el.textContent ?? "").toMatch(/Nov 4/);
      expect(el.textContent ?? "").toMatch(/BMO/);
      expect(el.className).not.toContain("theta-earnings--warn");
      expect(el.getAttribute("data-within-dte")).toBe("false");
    }

    const missing = cells.filter((el) => el.textContent === "---");
    expect(missing.length).toBe(1);
    for (const el of missing) {
      expect(el.className).toContain("theta-earnings--empty");
    }

    // Only the within-DTE row wears the ER badge.
    const grid = screen.getByTestId("theta-grid");
    expect(within(grid).getByText(/^ER TODAY AMC$/)).toBeTruthy();
    expect(within(screen.getByTestId("theta-grid-row-MSFT")).queryByText(/^ER /)).toBeNull();

    // The expanded detail carries the full earnings cell + its help bubble.
    fireEvent.click(within(grid).getByRole("button", { name: "HONA details" }));
    const detail = screen.getByTestId("theta-grid-detail-HONA");
    const detailCell = within(detail).getByTestId("theta-earnings-cell");
    expect(detailCell.className).toContain("theta-earnings--hot");

    const earningsTrigger = screen.getByTestId("theta-harvester-tooltip-earnings");
    fireEvent.mouseEnter(earningsTrigger);
    expect(screen.getByTestId("theta-harvester-tooltip-content-earnings").textContent).toContain(
      "Next earnings date if it falls inside the short-strangle DTE window",
    );
    expect(screen.getByTestId("theta-harvester-tooltip-content-earnings").textContent).toContain("AMC=after close");
    fireEvent.mouseLeave(earningsTrigger);

    const mobileList = screen.getByTestId("theta-harvester-mobile-list");
    expect(within(mobileList).getAllByTestId("theta-earnings-mobile").length).toBe(3);
  });

  it("filter chips narrow the cohort and report the active filter", () => {
    const multi: ThetaHarvesterData = {
      ...data,
      results: [
        { ...data.results[0], ticker: "AAPL" },
        {
          ...data.results[0],
          ticker: "SLOW",
          score: 62,
          iv_rv_ratio: 1.2,
          structure: { ...data.results[0].structure, dte: 60 },
        },
      ],
    };
    render(<ThetaHarvesterScanner data={multi} />);
    const grid = screen.getByTestId("theta-grid");

    expect(screen.getByTestId("theta-filter-summary").textContent).toBe("NO FILTER");
    expect(within(grid).getAllByTestId(/theta-grid-row-/).length).toBe(2);

    fireEvent.click(within(grid).getByRole("button", { name: "DTE ≤ 30" }));
    expect(within(grid).getAllByTestId(/theta-grid-row-/).length).toBe(1);
    expect(within(grid).queryByTestId("theta-grid-row-SLOW")).toBeNull();
    expect(screen.getByTestId("theta-filter-summary").textContent).toBe("DTE ≤ 30 ACTIVE");

    fireEvent.click(within(grid).getByRole("button", { name: "All 2" }));
    expect(within(grid).getAllByTestId(/theta-grid-row-/).length).toBe(2);
  });

  it("renders criteria pips with pass/fail state from the gates", () => {
    const mixed: ThetaHarvesterData = {
      ...data,
      results: [
        {
          ...data.results[0],
          gates: { ...data.results[0].gates, iv_rich_vs_rv: false },
        },
      ],
    };
    render(<ThetaHarvesterScanner data={mixed} />);
    const row = screen.getByTestId("theta-grid-row-AAPL");
    expect(within(row).getByTitle("PASS · Delta neutrality within band").textContent).toBe("Δ");
    expect(within(row).getByTitle("NOT MET · IV rich vs realized").textContent).toBe("V");
    expect(within(row).getByTitle("PASS · Dealer positioning supportive").textContent).toBe("D");
    expect(within(row).getByTitle("PASS · Theta decay dominant").textContent).toBe("Θ");
  });

  it("multi-select shows the selection bar; Send to orders routes the first pick", () => {
    render(<ThetaHarvesterScanner data={data} />);
    expect(screen.queryByTestId("theta-selection-bar")).toBeNull();

    fireEvent.click(screen.getByRole("checkbox", { name: "Select AAPL" }));
    const bar = screen.getByTestId("theta-selection-bar");
    expect(bar.textContent).toContain("1 structure selected · AAPL");

    fireEvent.click(within(bar).getByRole("button", { name: "Send to orders" }));
    expectThetaHref(pushMock.mock.calls[0][0]);

    fireEvent.click(within(bar).getByRole("button", { name: "Clear" }));
    expect(screen.queryByTestId("theta-selection-bar")).toBeNull();
  });

  it("J/K move the cursor, Enter expands, Space selects — but never while typing", () => {
    const multi: ThetaHarvesterData = {
      ...data,
      results: [
        { ...data.results[0], ticker: "AAPL" },
        { ...data.results[0], ticker: "MSFT", score: 80 },
      ],
    };
    render(<ThetaHarvesterScanner data={multi} onTickerScan={vi.fn()} />);

    fireEvent.keyDown(document, { key: "j" });
    fireEvent.keyDown(document, { key: "Enter" });
    expect(screen.getByTestId("theta-grid-detail-MSFT")).toBeTruthy();

    fireEvent.keyDown(document, { key: "k" });
    fireEvent.keyDown(document, { key: " " });
    expect(screen.getByTestId("theta-selection-bar").textContent).toContain("AAPL");

    // Typing in the ticker search must not drive the table.
    const input = screen.getByLabelText("Ticker symbol");
    fireEvent.keyDown(input, { key: "j" });
    fireEvent.keyDown(input, { key: " " });
    expect(screen.getByTestId("theta-selection-bar").textContent).not.toContain("MSFT");
  });

  it("omitted earnings field displays as ---", () => {
    const row = { ...data.results[0] } as ThetaHarvesterResult;
    delete row.earnings;
    render(<ThetaHarvesterScanner data={{ ...data, results: [row] }} />);
    const cells = screen.getAllByTestId("theta-earnings-cell");
    expect(cells.every((el) => el.textContent === "---")).toBe(true);
  });
});
