// @vitest-environment jsdom
//
// REL-148 (R-414, R-415, R-425), web half.
//
// R-414: `prefillLabelForSource`'s `default` branch fires when `src` is absent,
// misspelled, or from a future seeder, so a hand-edited or shared
// `?legs=SELL:1x100P` URL is stamped PREFILLED FROM THETA HARVESTER -- a
// scanner verdict asserted over a contract no scanner produced.
//
// R-415: `new Date(lastSync).toLocaleTimeString()` prints a bare clock with no
// date, so a three-day-old `data/leap.json` reads as today's scan -- inline
// with the TRADE BEST order-entry link, which has no staleness gate at all.
//
// R-425: above 1180px the only inner scrollports are `.chain-grid-wrapper` and
// `.order-builder--rail`, so an error block is clipped with no scrollbar
// anywhere.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import LeapScanner from "../components/LeapScanner";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: never) =>
    React.createElement("a", { href, ...rest }, children),
}));

const CHAIN_SOURCE = readFileSync(
  resolve(__dirname, "../components/ticker-detail/OptionsChainTab.tsx"),
  "utf8",
);
const CSS = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");

function stripComments(text: string): string {
  return text
    .split("\n")
    .filter((line) => !line.trim().startsWith("//") && !line.trim().startsWith("*"))
    .join("\n");
}

afterEach(cleanup);

describe("prefill label provenance", () => {
  it("does not attribute an unknown src to a scanner", () => {
    const body = stripComments(CHAIN_SOURCE);
    const start = body.indexOf("function prefillLabelForSource");
    const end = body.indexOf("\n}", start);
    const fn = body.slice(start, end);
    expect(fn).toContain("PREFILLED FROM LINK");
    // The THETA wording must be reachable only from its own `case`.
    const thetaAt = fn.indexOf("PREFILLED FROM THETA HARVESTER");
    const defaultAt = fn.indexOf("default:");
    expect(thetaAt).toBeGreaterThan(-1);
    expect(defaultAt).toBeGreaterThan(thetaAt);
  });

  it("keeps the named seeders", () => {
    const fn = stripComments(CHAIN_SOURCE);
    expect(fn).toContain("PREFILLED FROM VOL CONE");
    expect(fn).toContain("PREFILLED FROM LEAP SCAN");
    expect(fn).toContain('case "theta"');
  });
});

function leapData(lastSync: string) {
  return {
    scan_time: lastSync,
    min_gap: 5,
    universe: "watchlist",
    results: [
      {
        ticker: "MU",
        price: 100,
        current_iv: 20,
        iv_rank: 50,
        hv_20: 30,
        hv_252: 28,
        leap_count: 4,
        is_mispriced: true,
        best_gap: 12,
        best_leap: {
          symbol: "MU270115C00600000",
          expiry: "2027-01-15",
          strike: 600,
          right: "C",
          iv: 12,
          delta: 0.5,
          gap: 18,
          oi: 4200,
          volume: 300,
        },
      },
    ],
  };
}

describe("LEAP scan staleness", () => {
  it("renders an age, not a bare clock", () => {
    const threeHoursAgo = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
    render(React.createElement(LeapScanner, { data: leapData(threeHoursAgo), lastSync: threeHoursAgo } as never));
    const meta = screen.getByTestId("leap-scan-age");
    expect(meta.textContent).toMatch(/\d/);
    expect(meta.textContent).not.toMatch(/^\d{1,2}:\d{2}:\d{2}/);
  });

  it("suppresses the TRADE BEST link past the staleness threshold", () => {
    const threeDaysAgo = new Date(Date.now() - 72 * 60 * 60 * 1000).toISOString();
    render(React.createElement(LeapScanner, { data: leapData(threeDaysAgo), lastSync: threeDaysAgo } as never));
    expect(screen.queryByTestId("leap-best-order-link")).toBeNull();
    expect(screen.getByTestId("leap-scan-age").textContent).toMatch(/stale/i);
  });

  it("keeps the link for a fresh scan", () => {
    const tenMinutesAgo = new Date(Date.now() - 10 * 60 * 1000).toISOString();
    render(React.createElement(LeapScanner, { data: leapData(tenMinutesAgo), lastSync: tenMinutesAgo } as never));
    expect(screen.getByTestId("leap-best-order-link")).toBeTruthy();
  });
});

describe("chain deck scrollability", () => {
  it("gives the chain tab a last-resort scrollport above 1180px", () => {
    const start = CSS.indexOf(".chain-tab {");
    const rule = CSS.slice(start, CSS.indexOf("}", start));
    expect(rule).toContain("overflow-y: auto");
  });
});
