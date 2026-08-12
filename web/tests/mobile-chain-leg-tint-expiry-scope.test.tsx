// @vitest-environment jsdom
//
// The mobile ladder tints cells that are already in the pending order. Since
// the builder survives an expiry change, that tint MUST be scoped to the
// visible expiry — otherwise a leg built on another expiry lights up an
// unrelated strike on the current ladder.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import MobileChainLadder from "../components/mobile/MobileChainLadder";
import { optionKey } from "../lib/pricesProtocol";
import type { OrderLeg } from "../lib/optionsChainUtils";

const NEAR = "20260821";
const FAR = "20260918";
const TICKER = "MU";

function strikeRow(strike: number) {
  return {
    strike,
    callKey: optionKey({ symbol: TICKER, expiry: NEAR, strike, right: "C" }),
    putKey: optionKey({ symbol: TICKER, expiry: NEAR, strike, right: "P" }),
  };
}

function leg(expiry: string): OrderLeg {
  return {
    id: `${TICKER}_${expiry}_970_C`,
    action: "BUY",
    right: "C",
    strike: 970,
    expiry,
    quantity: 1,
    limitPrice: null,
    priceManuallySet: false,
  };
}

function renderLadder(legs: OrderLeg[]) {
  Object.defineProperty(Element.prototype, "scrollTo", { configurable: true, value: vi.fn() });
  return render(
    React.createElement(MobileChainLadder, {
      ticker: TICKER,
      expirations: [NEAR, FAR],
      selectedExpiry: NEAR,
      onSelectExpiry: vi.fn(),
      visibleStrikes: [strikeRow(960), strikeRow(970)],
      atmStrike: 970,
      prices: {},
      currentPrice: 967.78,
      sideFilter: "both" as const,
      onSideFilterChange: vi.fn(),
      strikesPerSide: 15,
      onStrikesPerSideChange: vi.fn(),
      orderLegs: legs,
      portfolio: null,
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Mobile chain ladder leg tint", () => {
  it("tints a cell whose leg is on the visible expiry", () => {
    renderLadder([leg(NEAR)]);
    const cell = screen.getByTestId("mobile-chain-call-970");
    expect(cell.getAttribute("aria-pressed")).toBe("true");
    expect(cell.className).toContain("mobile-chain__cell--selected-buy");
  });

  it("leaves the cell untinted when the leg belongs to another expiry", () => {
    renderLadder([leg(FAR)]);
    const cell = screen.getByTestId("mobile-chain-call-970");
    expect(cell.getAttribute("aria-pressed")).toBe("false");
    expect(cell.className).not.toContain("selected-buy");
  });
});
