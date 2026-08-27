/**
 * @vitest-environment jsdom
 *
 * Expanded leg rows: a SHORT option leg's Avg Entry, Last Price, and Implied
 * are premium CREDITS and must render negative, matching the signed combo
 * header row (META ratio RR bug 2026-08-23: SHORT 25x Put $550 showed
 * $12.50 / C$13.58 / $12.04 all positive).
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import PositionTable from "../components/PositionTable";
import { seedRiskFreeRateForTests } from "@/lib/useRiskFreeRate";
import { bsPut } from "../lib/blackScholes";
import { yearsToExpiry } from "../lib/impliedValue";
import type { PortfolioPosition } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(cleanup);
beforeEach(() => {
  // R-229: the Implied columns now render "—" until FRED resolves, so this
  // file's expected values — computed with r = 0 — were silently relying on
  // the old unresolved-means-zero default. Seed a RESOLVED 0%, which is a
  // legal reading, so the arithmetic below is unchanged and the assumption is
  // explicit. The unresolved case is asserted in
  // risk-free-rate-unavailable.test.tsx.
  seedRiskFreeRateForTests(0);
});

beforeEach(() => {
  window.localStorage.clear();
});

const TODAY = new Date();

function pd(over: Partial<PriceData>): PriceData {
  return {
    symbol: "X",
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: TODAY.toISOString(),
    ...over,
  };
}

const expiry = "2099-05-01"; // far future so T > 0 regardless of when the test runs

const RATIO_RR: PortfolioPosition = {
  id: 13,
  ticker: "TSLA",
  structure: "Ratio Risk Reversal 75x10 (P$400.0/C$410.0)",
  structure_type: "Ratio Risk Reversal",
  risk_profile: "undefined",
  expiry,
  contracts: 75,
  direction: "COMBO",
  entry_cost: 118200,
  max_risk: null,
  market_value: 51975,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-04-15",
  legs: [
    {
      direction: "LONG",
      contracts: 75,
      type: "Call",
      strike: 410,
      entry_cost: 145875,
      avg_cost: 1945,
      market_price: 10.45,
      market_value: 78375,
    },
    {
      direction: "SHORT",
      contracts: 10,
      type: "Put",
      strike: 400,
      entry_cost: -27690,
      avg_cost: -2769,
      market_price: 26.41,
      market_value: -26410,
    },
  ],
};

function expandLegs() {
  fireEvent.click(screen.getByLabelText("Expand legs for TSLA"));
}

describe("PositionTable expanded leg rows — SHORT option leg sign", () => {
  it("renders the SHORT leg's Avg Entry and Last Price as negative premiums", () => {
    render(<PositionTable positions={[RATIO_RR]} prices={{}} />);
    expandLegs();

    const shortRow = screen.getByText("SHORT Put $400").closest("tr")!;
    expect(shortRow.textContent).toContain("$-27.69");
    expect(shortRow.textContent).toContain("$-26.41");
  });

  it("keeps the LONG leg's Avg Entry and Last Price positive debits", () => {
    render(<PositionTable positions={[RATIO_RR]} prices={{}} />);
    expandLegs();

    const longRow = screen.getByText("LONG Call $410").closest("tr")!;
    expect(longRow.textContent).toContain("$19.45");
    expect(longRow.textContent).toContain("$10.45");
    expect(longRow.textContent).not.toContain("$-19.45");
    expect(longRow.textContent).not.toContain("$-10.45");
  });

  it("renders the SHORT leg's Implied (BS) per-contract value negative", () => {
    const sigma = 0.45;
    const spot = 405;
    const prices: Record<string, PriceData> = {
      TSLA: pd({ last: spot }),
      [`TSLA_${expiry.replace(/-/g, "")}_410_C`]: pd({ impliedVol: sigma }),
      [`TSLA_${expiry.replace(/-/g, "")}_400_P`]: pd({ impliedVol: sigma }),
    };
    render(<PositionTable positions={[RATIO_RR]} prices={prices} />);
    expandLegs();

    const T = yearsToExpiry(expiry, new Date())!;
    const putPerShare = bsPut(spot, 400, T, 0, sigma);
    const shortRow = screen.getByText("SHORT Put $400").closest("tr")!;
    expect(shortRow.textContent).toContain(`$-${putPerShare.toFixed(2)}`);
  });
});
