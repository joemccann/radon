// @vitest-environment jsdom
//
// The mobile ladder tints cells that are already in the pending order. Since
// the builder survives an expiry change, that tint MUST be scoped to the
// visible expiry — otherwise a leg built on another expiry lights up an
// unrelated strike on the current ladder.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

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

function renderLadder(legs: OrderLeg[], prices: Record<string, never> = {}, sideFilter: "both" | "calls" = "both") {
  Object.defineProperty(Element.prototype, "scrollTo", { configurable: true, value: vi.fn() });
  return render(
    React.createElement(MobileChainLadder, {
      ticker: TICKER,
      expirations: [NEAR, FAR],
      selectedExpiry: NEAR,
      onSelectExpiry: vi.fn(),
      visibleStrikes: [strikeRow(960), strikeRow(970)],
      atmStrike: 970,
      prices,
      currentPrice: 967.78,
      sideFilter,
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
  vi.unstubAllGlobals();
});

describe("Mobile chain ladder leg tint", () => {
  it("updates reserved ladder space when the pending strip resizes and disconnects its observer", () => {
    let height = 61; let resized: () => void = () => {}; const disconnect = vi.fn(); const observe = vi.fn();
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: () => void) { resized = callback; }
      observe = observe; disconnect = disconnect;
    });
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
      return { x: 0, y: 0, top: 0, left: 0, right: 393, bottom: height, width: 393, height: (this as HTMLElement).dataset.testid === 'mobile-chain-pending-strip' ? height : 0, toJSON: () => ({}) } as DOMRect;
    });
    const { unmount } = renderLadder([leg(NEAR)]);
    expect(observe).toHaveBeenCalledWith(screen.getByTestId('mobile-chain-pending-strip'));
    height = 86; act(() => resized());
    expect(screen.getByTestId('mobile-chain-ladder').style.paddingBottom).toBe('86px');
    unmount(); expect(disconnect).toHaveBeenCalledOnce();
  });
  it("reserves the measured pending strip and removes it while a detail sheet is open", () => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      return { x: 0, y: 0, top: 0, left: 0, right: 393, bottom: 61, width: 393, height: (this as HTMLElement).dataset.testid === "mobile-chain-pending-strip" ? 61 : 0, toJSON: () => ({}) } as DOMRect;
    });
    renderLadder([leg(NEAR)]);
    expect(screen.getByTestId("mobile-chain-ladder").style.paddingBottom).toBe("61px");
    expect(screen.getByTestId("mobile-chain-pending-strip")).toBeTruthy();
    fireEvent.click(screen.getByTestId("mobile-chain-call-970"));
    expect(screen.getByTestId("mobile-chain-detail-sheet")).toBeTruthy();
    expect(screen.queryByTestId("mobile-chain-pending-strip")).toBeNull();
  });

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

  it("labels average volume as volume rather than open interest", () => {
    const callKey = strikeRow(970).callKey;
    renderLadder([], {
      [callKey]: {
        symbol: TICKER,
        avgVolume: 12_345,
      } as never,
    }, "calls");
    const cell = screen.getByTestId("mobile-chain-call-970");
    expect(cell.textContent).toContain("AVG VOL 12k");
    expect(cell.textContent).not.toContain("OI 12k");
  });
});
