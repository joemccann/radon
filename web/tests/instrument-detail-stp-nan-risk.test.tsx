/**
 * @vitest-environment jsdom
 *
 * R-322 / REL-111: a STP ticket must not arm on a NaN risk verdict.
 *
 * The opening (non-close-out) branch of `riskInput` built `netPremium` from
 * `parsedPrice` — the raw Limit input — where `totalCost`, `description` and
 * the close-out branch all use the `riskPrice` resolved by
 * `riskPriceForOrderType`. For a STP order `pricesValidForOrderType` returns
 * `stopOk` alone, so a BLANK Limit with a valid Stop passes `isValid` while
 * `parseFloat("")` is NaN. That NaN reached `useOrderRisk`, every max-loss /
 * max-gain / breakeven rendered NaN, and `okToSubmit` — which is only
 * `coverageStatus === "resolved"` — still armed Transmit on a meaningless
 * risk verdict. `OrderTab.tsx` does it correctly.
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import InstrumentDetailModal from "../components/InstrumentDetailModal";
import { useOrderRisk } from "@/lib/order/risk";
import { renderHook } from "@testing-library/react";
import type { PortfolioData, PortfolioLeg, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock("../components/Modal", () => ({
  default: ({ children, title }: { children: React.ReactNode; title: string }) => (
    <div role="dialog" aria-label={title} data-testid="mock-modal">
      {children}
    </div>
  ),
}));

function price(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol, last: (bid + ask) / 2, lastIsCalculated: false, bid, ask,
    bidSize: 1, askSize: 1, volume: 1, high: null, low: null, open: null,
    close: null, week52High: null, week52Low: null, avgVolume: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null,
    undPrice: null, timestamp: new Date().toISOString(),
  };
}

const LONG_CALL: PortfolioLeg = {
  direction: "LONG",
  contracts: 5,
  type: "Call",
  strike: 100,
  entry_cost: 2_500,
  avg_cost: 500,
  market_price: 5.0,
  market_price_is_calculated: false,
  market_value: 2_500,
};

const POSITION: PortfolioPosition = {
  id: 7, ticker: "ABC", structure: "Long Call $100.0",
  structure_type: "Long Call", risk_profile: "defined", direction: "LONG",
  contracts: 5, expiry: "2026-12-18", entry_date: "2026-08-01",
  entry_cost: 2_500, market_value: 2_500, max_risk: null,
  kelly_optimal: null, target: null, stop: null, legs: [LONG_CALL],
} as unknown as PortfolioPosition;

const PORTFOLIO: PortfolioData = {
  bankroll: 100_000, peak_value: 100_000, last_sync: new Date().toISOString(),
  total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
  position_count: 1, defined_risk_count: 1, undefined_risk_count: 0,
  avg_kelly_optimal: null, positions: [POSITION],
  account_summary: { available_funds: 500_000, buying_power: 1_000_000 },
} as unknown as PortfolioData;

const PRICES: Record<string, PriceData> = {
  ABC_20261218_100_C: price("ABC_20261218_100_C", 4.9, 5.1),
};

function renderModal() {
  return render(
    <InstrumentDetailModal
      leg={LONG_CALL}
      ticker="ABC"
      expiry="2026-12-18"
      prices={PRICES}
      portfolio={PORTFOLIO}
      onClose={() => {}}
    />,
  );
}

/** BUY on a held LONG opens fresh exposure, so `isClosingHeld` is false. */
function armStpBuyWithBlankLimit() {
  fireEvent.click(screen.getByRole("button", { name: "BUY" }));
  fireEvent.click(screen.getByTestId("order-type-stp"));
  fireEvent.change(screen.getByTestId("order-stop-price"), { target: { value: "4.00" } });
}

function summaryText(): string {
  return document.querySelector(".order-confirm-summary")?.textContent ?? "";
}

describe("InstrumentDetailModal STP risk verdict", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders finite risk figures for a STP buy with a blank limit", () => {
    renderModal();
    armStpBuyWithBlankLimit();
    fireEvent.click(screen.getByRole("button", { name: /place order/i }));

    const text = summaryText();
    expect(text).not.toContain("NaN");
    expect(text).toContain("$4.00");
  });

  it("does not fire a placement request while the ticket is still unarmed", () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }) as Response);
    vi.stubGlobal("fetch", fetchMock);

    renderModal();
    armStpBuyWithBlankLimit();

    const placements = fetchMock.mock.calls.filter(([input]) =>
      String(input) === "/api/orders/place",
    );
    expect(placements).toHaveLength(0);
  });

  it("sends a finite stop price on the wire when armed and confirmed", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => ({
      ok: true,
      json: async () =>
        String(input) === "/api/orders/whatif" ? { initMargin: 1_000 } : { status: "submitted" },
    }) as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    renderModal();
    armStpBuyWithBlankLimit();
    fireEvent.click(screen.getByRole("button", { name: /place order/i }));
    fireEvent.click(screen.getByRole("button", { name: /confirm order/i }));

    const placements = fetchMock.mock.calls.filter(([input]) =>
      String(input) === "/api/orders/place",
    );
    expect(placements).toHaveLength(1);
    const body = JSON.parse(placements[0][1]!.body as string);
    expect(body.orderType).toBe("STP");
    expect(Number.isFinite(body.stopPrice)).toBe(true);
    expect(body.stopPrice).toBe(4);
    expect(body.limitPrice).toBeUndefined();
  });
});

describe("useOrderRisk finiteness gate", () => {
  afterEach(cleanup);

  it("refuses to arm submit when netPremium is non-finite", () => {
    const { result } = renderHook(() =>
      useOrderRisk(
        {
          ticker: "ABC",
          chainLegs: [
            { action: "BUY", right: "C", strike: 100, expiry: "2026-12-18", quantity: 5 },
          ],
          netPremium: Number.NaN,
          description: "BUY 5x ABC 100C @ $NaN",
          totalCost: Number.NaN,
        },
        PORTFOLIO,
      ),
    );

    expect(result.current.coverageStatus).toBe("resolved");
    expect(result.current.okToSubmit).toBe(false);
  });

  it("still arms submit for a finite netPremium", () => {
    const { result } = renderHook(() =>
      useOrderRisk(
        {
          ticker: "ABC",
          chainLegs: [
            { action: "BUY", right: "C", strike: 100, expiry: "2026-12-18", quantity: 5 },
          ],
          netPremium: 4,
          description: "BUY 5x ABC 100C @ $4.00",
          totalCost: 2_000,
        },
        PORTFOLIO,
      ),
    );

    expect(result.current.okToSubmit).toBe(true);
  });
});
