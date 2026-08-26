/**
 * @vitest-environment jsdom
 *
 * The mobile order ticket must show the operator the SAME nine-field quote
 * telemetry the portfolio position drawer gives them: BID MID ASK / SPREAD
 * LAST(or MARK) VOLUME / HIGH LOW DAY — for the exact instrument they are
 * about to trade.
 *
 * Single leg  -> the leg's real PriceData out of the `prices` map.
 * Combo       -> the signed net quote wrapped by `comboQuotePriceData`, so the
 *                spread math stays in `buildQuoteTelemetryModel`.
 *
 * The Bid/Mid/Ask quick-set chips are a real feature (one tap fills the limit
 * input) and must survive the addition of the panel.
 */

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import MobileOrderTicket from "../components/mobile/MobileOrderTicket";
import type { OrderLeg } from "@/lib/optionsChainUtils";
import type { PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("@/components/ComboSkewPanel", () => ({ default: () => null }));

const TICKER = "AAPL";
const EXPIRY = "20260320";

const NINE_FIELDS = ["BID", "MID", "ASK", "SPREAD", "VOLUME", "HIGH", "LOW", "DAY"];

function makePrice(
  symbol: string,
  bid: number,
  ask: number,
  session: Partial<PriceData> = {},
): PriceData {
  return {
    symbol,
    last: (bid + ask) / 2,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 1,
    askSize: 1,
    volume: 1234,
    high: 4.1,
    low: 2.6,
    open: 3.0,
    close: 3.0,
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
    ...session,
  };
}

const CALL_KEY = `${TICKER}_${EXPIRY}_200_C`;
const PUT_KEY = `${TICKER}_${EXPIRY}_190_P`;

const PRICES: Record<string, PriceData> = {
  [CALL_KEY]: makePrice(CALL_KEY, 3.0, 3.4),
  [PUT_KEY]: makePrice(PUT_KEY, 1.0, 1.2),
};

const EMPTY_PORTFOLIO = { positions: [] } as unknown as PortfolioData;

function makeLeg(overrides: Partial<OrderLeg> = {}): OrderLeg {
  return {
    id: CALL_KEY,
    action: "BUY",
    right: "C",
    strike: 200,
    expiry: EXPIRY,
    quantity: 1,
    limitPrice: null,
    ...overrides,
  };
}

function renderTicket(legs: OrderLeg[]) {
  return render(
    <MobileOrderTicket
      open
      ticker={TICKER}
      legs={legs}
      prices={PRICES}
      spot={210}
      portfolio={EMPTY_PORTFOLIO}
      onClose={() => {}}
      onRemoveLeg={() => {}}
      onUpdateLeg={() => {}}
      onClearLegs={() => {}}
    />,
  );
}

// BottomSheet portals to document.body, so the sheet body is NOT inside the
// render container.
function labelTexts(): string[] {
  return Array.from(document.body.querySelectorAll(".price-bar-label")).map(
    (node) => node.textContent ?? "",
  );
}

function valueFor(label: string): string {
  const row = Array.from(document.body.querySelectorAll(".price-bar-item")).find(
    (node) => node.querySelector(".price-bar-label")?.textContent === label,
  );
  return row?.querySelector(".price-bar-value")?.textContent ?? "";
}

afterEach(() => cleanup());

describe("MobileOrderTicket quote telemetry", () => {
  it("renders the full nine-field telemetry for a single-leg ticket", () => {
    renderTicket([makeLeg()]);

    const labels = labelTexts();
    for (const field of NINE_FIELDS) {
      expect(labels).toContain(field);
    }
    expect(labels).toContain("LAST");

    expect(valueFor("BID")).toBe("$3.00");
    expect(valueFor("MID")).toBe("$3.20");
    expect(valueFor("ASK")).toBe("$3.40");
    expect(valueFor("VOLUME")).toBe("1,234");
    expect(valueFor("HIGH")).toBe("$4.10");
    expect(valueFor("LOW")).toBe("$2.60");
  });

  it("uses the tight density inside the bottom sheet", () => {
    renderTicket([makeLeg()]);
    expect(document.body.querySelector(".price-bar--tight")).not.toBeNull();
  });

  it("renders the nine fields off the signed net quote on a combo ticket", () => {
    renderTicket([
      makeLeg(),
      makeLeg({ id: PUT_KEY, action: "SELL", right: "P", strike: 190 }),
    ]);

    const labels = labelTexts();
    for (const field of NINE_FIELDS) {
      expect(labels).toContain(field);
    }
    // A combo has no traded print, so the panel reads MARK, not LAST.
    expect(labels).toContain("MARK");

    // BUY call ask 3.40 - SELL put bid 1.00 = 2.40 net ask;
    // BUY call bid 3.00 - SELL put ask 1.20 = 1.80 net bid.
    expect(valueFor("BID")).toBe("$1.80");
    expect(valueFor("ASK")).toBe("$2.40");
    expect(valueFor("MID")).toBe("$2.10");
    // No exchange publishes a combo's session OHLV.
    expect(valueFor("VOLUME")).toBe("---");
    expect(valueFor("HIGH")).toBe("---");
  });

  it("keeps the Bid/Mid/Ask chips tappable so one tap still fills the limit", () => {
    renderTicket([makeLeg()]);

    fireEvent.click(screen.getByTestId("mobile-order-ticket-quote-ask"));

    expect(
      (screen.getByTestId("mobile-order-ticket-price-input") as HTMLInputElement).value,
    ).toBe("3.40");
  });
});
