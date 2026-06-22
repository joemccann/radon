/**
 * @vitest-environment jsdom
 *
 * SHORT stock P&L must be sign-correct. A short LOSES when price rises.
 * `pos.contracts` is a positive magnitude (direction lives on the position/leg),
 * so a real-time `price × contracts` market value must carry the direction sign.
 * Bug (2026-06-22): the live stock path used `rtStockLast * pos.contracts`
 * (always positive), and `mv - resolveEntryCost` (signed negative for a short)
 * became a SUM — MU short −1000 @ $1,134.97, last $1,191.09 showed +$2,292,769
 * (+202%) instead of the true ≈ −$56,121 loss. Same class in getTodayPnlDollars.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import PositionTable from "../components/PositionTable";
import { positionDirectionSign, getTodayPnlDollars } from "../lib/positionUtils";
import type { PortfolioPosition } from "../lib/types";
import type { PriceData } from "../lib/pricesProtocol";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../components/InstrumentDetailModal", () => ({ default: () => null }));

afterEach(cleanup);
beforeEach(() => window.localStorage.clear());

function pd(over: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "X", last: null, lastIsCalculated: false, bid: null, ask: null,
    bidSize: null, askSize: null, volume: null, high: null, low: null,
    open: null, close: null, week52High: null, week52Low: null, avgVolume: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(), ...over,
  };
}

// MU short 1000 @ $1,134.97; entry_cost/market_value negative (IB short convention).
const MU_SHORT: PortfolioPosition = {
  id: 1, ticker: "MU", structure: "Stock (-1000.0 shares)", structure_type: "Stock",
  risk_profile: "equity", expiry: "N/A", contracts: 1000, direction: "SHORT",
  entry_cost: -1134969.41, max_risk: null, market_value: -1191090, kelly_optimal: null,
  target: null, stop: null, entry_date: "2026-06-01",
  legs: [{ direction: "SHORT", contracts: 1000, type: "Stock", strike: 0,
    entry_cost: 1134969.41, avg_cost: 1134.97, market_price: 1191.09, market_value: 1191090 }],
};
const MSFT_LONG: PortfolioPosition = {
  id: 2, ticker: "MSFT", structure: "Stock (1000.0 shares)", structure_type: "Stock",
  risk_profile: "equity", expiry: "N/A", contracts: 1000, direction: "LONG",
  entry_cost: 468505, max_risk: 468505, market_value: 378100, kelly_optimal: null,
  target: null, stop: null, entry_date: "2026-06-01",
  legs: [{ direction: "LONG", contracts: 1000, type: "Stock", strike: 0,
    entry_cost: 468505, avg_cost: 468.51, market_price: 378.10, market_value: 378100 }],
};

describe("positionDirectionSign", () => {
  it("is -1 for a short, +1 for a long", () => {
    expect(positionDirectionSign(MU_SHORT)).toBe(-1);
    expect(positionDirectionSign(MSFT_LONG)).toBe(1);
  });
});

describe("getTodayPnlDollars sign for stocks", () => {
  it("SHORT loses on an up day", () => {
    // last 1191.09 > close 1157.80 → +33.29/sh; short 1000 → −$33,290.
    const v = getTodayPnlDollars(MU_SHORT, { MU: pd({ last: 1191.09, close: 1157.80 }) });
    expect(v).not.toBeNull();
    expect(v!).toBeLessThan(0);
    expect(Math.round(v!)).toBe(-33290);
  });
  it("LONG gains on an up day", () => {
    const v = getTodayPnlDollars(MSFT_LONG, { MSFT: pd({ last: 380, close: 378 }) });
    expect(v!).toBeGreaterThan(0);
    expect(Math.round(v!)).toBe(2000);
  });
});

describe("PositionTable total P&L sign for a short stock", () => {
  it("a short whose price rose shows a LOSS, not a phantom gain", () => {
    render(<PositionTable positions={[MU_SHORT]} prices={{ MU: pd({ last: 1191.09, close: 1157.80 }) }} />);
    const tr = screen.getByText("MU").closest("tr")!;
    const text = tr.textContent ?? "";
    // True P&L = mv(−1,191,090) − entryCost(−1,134,969) ≈ −$56,121.
    expect(text).toMatch(/-\$56,1\d\d/);
    // The bug rendered the additive phantom gain — must be gone.
    expect(text).not.toContain("2,292,769");
    expect(text).not.toMatch(/\+\$2,/);
  });

  it("a long stock P&L is unchanged (control)", () => {
    render(<PositionTable positions={[MSFT_LONG]} prices={{ MSFT: pd({ last: 378.10, close: 379 }) }} />);
    const tr = screen.getByText("MSFT").closest("tr")!;
    // mv 378,100 − entryCost 468,505 = −$90,405.
    expect(tr.textContent ?? "").toMatch(/-\$90,40\d/);
  });
});
