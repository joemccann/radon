// @vitest-environment jsdom
//
// REL-143(b) / R-388: the TRADE BEST link ranks and labels on the honest
// per-contract figure. `best_leap` was assigned under
// `gap_20 > best_gap or best_leap is None`, so a ticker mispriced only via
// `gap_60` got a contract while `best_gap` stayed at its `0` initialiser --
// rendering a MISPRICED row whose clickable anchor text is `+0.0` while still
// arming a live contract. `widestMispriced` then ranked the fleet-wide button
// by that same `best_gap`, so a ticker whose linked contract's real gap is 18
// vol points lost the headline slot to one whose contract gap is 6. The honest
// figure is already in the payload (`LeapBestContract.gap`) and nothing read it.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import LeapScanner from "../components/LeapScanner";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: never) =>
    React.createElement("a", { href, ...rest }, children),
}));

function row(ticker: string, bestGap: number, contractGap: number | null) {
  return {
    ticker,
    price: 100,
    current_iv: 20,
    iv_rank: 50,
    hv_20: 30,
    hv_252: 28,
    leap_count: 4,
    is_mispriced: true,
    best_gap: bestGap,
    best_leap:
      contractGap == null
        ? null
        : {
            symbol: `${ticker}270115C00600000`,
            expiry: "2027-01-15",
            strike: 600,
            right: "C",
            iv: 12,
            delta: 0.5,
            gap: contractGap,
            oi: 4200,
            volume: 300,
          },
  };
}

function renderScanner(rows: unknown[]) {
  return render(
    React.createElement(LeapScanner, {
      data: { results: rows, min_gap: 5, universe: "watchlist", generated_at: new Date().toISOString() },
      loading: false,
      onScan: vi.fn(),
      onTickerScan: vi.fn(),
    } as never),
  );
}

afterEach(cleanup);

describe("LEAP headline contract", () => {
  it("ranks TRADE BEST by the contract's own gap, not the group gap", () => {
    // MU's linked contract is worth 18 vol points; NVDA's headline group gap is
    // wider but its linked contract is only worth 7.
    renderScanner([row("NVDA", 9, 7), row("MU", 6, 18)]);
    const best = screen.getByTestId("leap-best-order-link");
    expect(best.getAttribute("href")).toContain("MU");
  });

  it("labels the row anchor with the contract's own gap", () => {
    renderScanner([row("MU", 0, 18)]);
    const link = screen.getByTestId("leap-order-link-MU");
    expect(link.textContent).toContain("18");
    expect(link.textContent).not.toContain("+0.0");
  });

  it("does not render a plus-zero beside MISPRICED", () => {
    renderScanner([row("MU", 0, 18)]);
    expect(screen.queryByText("+0.0")).toBeNull();
  });

  it("renders no link at all when no contract was promoted", () => {
    renderScanner([row("MU", 6, null)]);
    expect(screen.queryByTestId("leap-order-link-MU")).toBeNull();
    expect(screen.queryByTestId("leap-best-order-link")).toBeNull();
  });
});
