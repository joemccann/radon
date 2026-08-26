/**
 * @vitest-environment jsdom
 *
 * PositionTradeTicket must render the same nine-field quote telemetry block the
 * portfolio position drawer gives the operator: BID MID ASK / SPREAD LAST
 * VOLUME / HIGH LOW DAY — for a single leg AND for the combo net quote — while
 * keeping the BID/MID/ASK quick-fill buttons that set the limit price.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import PositionTradeTicket from "@/components/ticker-detail/PositionTradeTicket";
import { legPriceKey } from "@/lib/positionUtils";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioPosition } from "@/lib/types";
import type { TradeTarget } from "@/lib/order/positionTrade";

afterEach(cleanup);

const NINE_LABELS = ["BID", "MID", "ASK", "SPREAD", "VOLUME", "HIGH", "LOW", "DAY"];

function makePriceData(overrides: Partial<PriceData> & { symbol: string }): PriceData {
  return {
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
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

function riskReversal(): PortfolioPosition {
  return {
    id: 7,
    ticker: "MU",
    structure: "Risk Reversal (P$800.0/C$1050.0)",
    structure_type: "Risk Reversal",
    direction: "COMBO",
    contracts: 5,
    expiry: "2026-07-17",
    entry_date: "2026-05-29",
    entry_cost: -3495,
    market_value: -46290,
    market_price_is_calculated: false,
    legs: [
      {
        direction: "SHORT",
        type: "Call",
        strike: 1050,
        contracts: 3,
        avg_cost: 10999,
        entry_cost: -32997,
        market_price: 133.93,
        market_price_is_calculated: false,
      },
      {
        direction: "LONG",
        type: "Put",
        strike: 800,
        contracts: 5,
        avg_cost: 5900,
        entry_cost: 29500,
        market_price: 41.0,
        market_price_is_calculated: false,
      },
    ],
  } as unknown as PortfolioPosition;
}

function pricesFor(position: PortfolioPosition): Record<string, PriceData> {
  const callKey = legPriceKey(position.ticker, position.expiry, position.legs[0])!;
  const putKey = legPriceKey(position.ticker, position.expiry, position.legs[1])!;
  return {
    [callKey]: makePriceData({
      symbol: callKey,
      bid: 132.0,
      ask: 136.0,
      last: 133.93,
      close: 130.0,
      volume: 812,
      high: 138.5,
      low: 129.75,
    }),
    [putKey]: makePriceData({
      symbol: putKey,
      bid: 40.5,
      ask: 41.5,
      last: 41.0,
      close: 43.25,
      volume: 4231,
      high: 44.1,
      low: 40.2,
    }),
  };
}

function renderTicket(target: TradeTarget) {
  const position = riskReversal();
  return render(
    <PositionTradeTicket
      position={position}
      prices={pricesFor(position)}
      portfolio={null}
      target={target}
      onClose={() => {}}
    />,
  );
}

function labelsIn(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".price-bar-label")].map((n) => n.textContent ?? "");
}

describe("PositionTradeTicket quote telemetry", () => {
  it("renders the nine-field telemetry block for a leg target", () => {
    const { container } = renderTicket({ kind: "leg", index: 1 });
    const labels = labelsIn(container);
    for (const label of NINE_LABELS) {
      expect(labels).toContain(label);
    }
    expect(labels).toContain("LAST");
    expect(container.querySelector(".price-bar")).not.toBeNull();
  });

  it("shows the leg's own session stats, not the underlying's", () => {
    const { container } = renderTicket({ kind: "leg", index: 1 });
    const values = [...container.querySelectorAll(".price-bar-value")].map((n) => n.textContent ?? "");
    expect(values.some((v) => v.includes("4,231") || v.includes("4231"))).toBe(true);
    expect(values.some((v) => v.includes("44.10"))).toBe(true);
    expect(values.some((v) => v.includes("40.20"))).toBe(true);
  });

  it("renders the nine-field telemetry block for a combo target from the net quote", () => {
    const { container } = renderTicket({ kind: "combo" });
    const labels = labelsIn(container);
    for (const label of NINE_LABELS) {
      expect(labels).toContain(label);
    }
    // A combo has no exchange-published last, so the panel reads MARK.
    expect(labels).toContain("MARK");
  });

  it("keeps the BID/MID/ASK quick-fill buttons wired to the limit price", () => {
    renderTicket({ kind: "leg", index: 1 });
    const limit = screen.getByTestId("position-trade-limit") as HTMLInputElement;
    fireEvent.click(screen.getByRole("button", { name: /^BID/ }));
    expect(limit.value).toBe("40.50");
    fireEvent.click(screen.getByRole("button", { name: /^ASK/ }));
    expect(limit.value).toBe("41.50");
  });
});
