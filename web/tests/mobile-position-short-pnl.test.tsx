/**
 * @vitest-environment jsdom
 *
 * Mobile portfolio: a SHORT stock's P&L must be sign-correct. `pos.contracts` is
 * a positive magnitude, so the live stock MV (`last × contracts`) needs the
 * direction sign — otherwise `mv - ec` (signed) becomes a SUM. Bug 2026-06-23:
 * MU short −1000 showed +$2,190,329 (+192.99%) = |MV| + |EC| instead of the true
 * ~+$79,609 gain (the stock fell below the short entry). Mirrors the desktop
 * PositionTable fix; the mobile list was missed.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

import MobilePositionList from "../components/mobile/MobilePositionList";
import type { PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

afterEach(cleanup);

function pd(over: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "X", last: null, lastIsCalculated: false, bid: null, ask: null, bidSize: null,
    askSize: null, volume: null, high: null, low: null, open: null, close: null, week52High: null,
    week52Low: null, avgVolume: null, delta: null, gamma: null, theta: null, vega: null,
    impliedVol: null, undPrice: null, timestamp: new Date().toISOString(), ...over,
  };
}

const MU_SHORT: PortfolioPosition = {
  id: 5, ticker: "MU", structure: "Stock (-1000.0 shares)", structure_type: "Stock",
  risk_profile: "equity", expiry: "N/A", contracts: 1000, direction: "SHORT",
  entry_cost: -1134969, max_risk: null, market_value: -1055360, kelly_optimal: null,
  target: null, stop: null, entry_date: "2026-06-01",
  legs: [{ direction: "SHORT", contracts: 1000, type: "Stock", strike: 0, entry_cost: 1134969, avg_cost: 1134.97, market_price: 1055.36, market_value: 1055360 }],
};
const MSFT_LONG: PortfolioPosition = {
  id: 6, ticker: "MSFT", structure: "Stock (1000.0 shares)", structure_type: "Stock",
  risk_profile: "equity", expiry: "N/A", contracts: 1000, direction: "LONG",
  entry_cost: 468505, max_risk: 468505, market_value: 372910, kelly_optimal: null,
  target: null, stop: null, entry_date: "2026-06-01",
  legs: [{ direction: "LONG", contracts: 1000, type: "Stock", strike: 0, entry_cost: 468505, avg_cost: 468.51, market_price: 372.91, market_value: 372910 }],
};

describe("MobilePositionList — short stock P&L sign", () => {
  it("a short whose price fell shows a GAIN ≈ +$79,609, not the +$2.19M additive phantom", () => {
    // mv = −(1055.36 × 1000) = −1,055,360; ec = −1,134,969; pnl = +79,609.
    const { container } = render(
      <MobilePositionList positions={[MU_SHORT]} prices={{ MU: pd({ last: 1055.36, close: 1211 }) }} />,
    );
    const text = container.textContent ?? "";
    expect(text).toMatch(/\+\$79,6\d\d/);
    expect(text).not.toContain("2,190,329");
  });

  it("a long stock P&L is unchanged (control): price below entry → loss", () => {
    // mv = +372,910; ec = 468,505; pnl = −95,595.
    const { container } = render(
      <MobilePositionList positions={[MSFT_LONG]} prices={{ MSFT: pd({ last: 372.91, close: 379 }) }} />,
    );
    expect(container.textContent ?? "").toMatch(/-\$95,59\d/);
  });
});
