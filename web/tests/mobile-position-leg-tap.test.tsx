/**
 * @vitest-environment jsdom
 *
 * Mobile portfolio: tapping an individual leg of an expanded position must
 * open the instrument detail view for THAT leg (same InstrumentDetailModal
 * the desktop PositionTable opens). Bug 2026-08-03: leg rows were inert divs,
 * so a covered call's stock/call legs couldn't be opened at all on mobile.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import MobilePositionList from "../components/mobile/MobilePositionList";
import type { PortfolioLeg, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const modalProps: Array<{ leg: PortfolioLeg; ticker: string; expiry: string }> = [];
vi.mock("../components/InstrumentDetailModal", () => ({
  default: (props: { leg: PortfolioLeg; ticker: string; expiry: string }) => {
    modalProps.push(props);
    return (
      <div data-testid="instrument-detail-modal">
        {props.ticker} {props.leg.type} {props.leg.strike ?? ""}
      </div>
    );
  },
}));

afterEach(() => {
  cleanup();
  modalProps.length = 0;
});

function pd(over: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "X", last: null, lastIsCalculated: false, bid: null, ask: null, bidSize: null,
    askSize: null, volume: null, high: null, low: null, open: null, close: null, week52High: null,
    week52Low: null, avgVolume: null, delta: null, gamma: null, theta: null, vega: null,
    impliedVol: null, undPrice: null, timestamp: new Date().toISOString(), ...over,
  };
}

const EWY_COVERED_CALL: PortfolioPosition = {
  id: 1, ticker: "EWY", structure: "Covered Call $165.0 (2500 shares)", structure_type: "Covered Call",
  risk_profile: "defined", expiry: "2026-08-07", contracts: 2500, direction: "LONG",
  entry_cost: 412139, max_risk: 412139, market_value: 385500, kelly_optimal: null,
  target: null, stop: null, entry_date: "2026-07-28",
  legs: [
    { direction: "LONG", contracts: 2500, type: "Stock", strike: null, entry_cost: 424657, avg_cost: 169.86, market_price: 157.9, market_value: 394750 },
    { direction: "SHORT", contracts: 25, type: "Call", strike: 165, entry_cost: 12518, avg_cost: -500.7, market_price: 4.0, market_value: 10000 },
  ],
};

function expandCard() {
  render(
    <MobilePositionList
      positions={[EWY_COVERED_CALL]}
      prices={{ EWY: pd({ last: 157.9, close: 155 }) }}
    />,
  );
  fireEvent.click(screen.getByLabelText("EWY Covered Call $165.0 (2500 shares)"));
}

describe("MobilePositionList — leg tap opens instrument detail", () => {
  it("tapping the short call leg opens the modal for that call", () => {
    expandCard();
    fireEvent.click(screen.getByText(/SHORT 25x Call \$165/));
    expect(screen.getByTestId("instrument-detail-modal")).toBeTruthy();
    const props = modalProps[modalProps.length - 1];
    expect(props.ticker).toBe("EWY");
    expect(props.expiry).toBe("2026-08-07");
    expect(props.leg.type).toBe("Call");
    expect(props.leg.strike).toBe(165);
    expect(props.leg.direction).toBe("SHORT");
  });

  it("tapping the stock leg opens the modal for the stock leg", () => {
    expandCard();
    fireEvent.click(screen.getByText(/LONG 2500x Stock/));
    const props = modalProps[modalProps.length - 1];
    expect(props.leg.type).toBe("Stock");
    expect(props.leg.direction).toBe("LONG");
  });

  it("tapping a leg does NOT collapse the card (tap must not bubble to the card toggle)", () => {
    expandCard();
    fireEvent.click(screen.getByText(/SHORT 25x Call \$165/));
    expect(screen.getByTestId("mobile-position-EWY-legs")).toBeTruthy();
  });
});
