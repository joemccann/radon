// @vitest-environment jsdom

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import MobileChainLadder from "../components/mobile/MobileChainLadder";
import { optionKey, type PriceData } from "../lib/pricesProtocol";

const EXPIRY = "20261016";

function props() {
  return {
    ticker: "SPY",
    expirations: [EXPIRY],
    selectedExpiry: EXPIRY,
    onSelectExpiry: vi.fn(),
    visibleStrikes: [95, 100, 105, 110].map((strike) => ({
      strike,
      callKey: optionKey({ symbol: "SPY", expiry: EXPIRY, strike, right: "C" }),
      putKey: optionKey({ symbol: "SPY", expiry: EXPIRY, strike, right: "P" }),
    })),
    atmStrike: 100,
    prices: {},
    currentPrice: 102,
    sideFilter: "both" as const,
    onSideFilterChange: vi.fn(),
    strikesPerSide: 15,
    onStrikesPerSideChange: vi.fn(),
    portfolio: null,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Mobile chain anchored browsing", () => {
  it("uses bid and ask as compact prices and reserves extra fields for single-side mode", () => {
    const initial = props();
    const quote = { bid: 1.2, ask: 1.4, last: 1.3, impliedVol: 0.4 } as PriceData;
    const prices = { [initial.visibleStrikes[1].callKey]: quote };
    const { rerender } = render(<MobileChainLadder {...initial} prices={prices} />);
    const cell = screen.getByTestId("mobile-chain-call-100");
    expect(cell.querySelector(".mobile-chain__bid-ask--primary")?.textContent).toBe("$1.20 x $1.40");
    expect(cell.querySelector(".mobile-chain__last")).toBeNull();
    expect(cell.querySelector(".mobile-chain__meta")).toBeNull();

    rerender(<MobileChainLadder {...initial} prices={prices} sideFilter="calls" />);
    expect(cell.querySelector(".mobile-chain__last")?.textContent).toBe("1.30");
    expect(cell.querySelector(".mobile-chain__meta")?.textContent).toContain("IV 40.0%");
  });

  it("updates each side's ITM tint without moving rows or flagging ticks within one strike interval", () => {
    const initial = props();
    const { rerender } = render(<MobileChainLadder {...initial} />);
    const call = screen.getByTestId("mobile-chain-call-105");
    const put = screen.getByTestId("mobile-chain-put-105");
    expect(call.classList.contains("mobile-chain__cell--itm")).toBe(false);
    expect(put.classList.contains("mobile-chain__cell--itm")).toBe(true);

    rerender(<MobileChainLadder {...initial} currentPrice={103} />);
    expect(within(screen.getByTestId("chain-spot-bar")).queryByText(/Spot moved/)).toBeNull();
    rerender(<MobileChainLadder {...initial} currentPrice={108} />);
    expect(call.classList.contains("mobile-chain__cell--itm")).toBe(true);
    expect(put.classList.contains("mobile-chain__cell--itm")).toBe(false);
    expect(call.getAttribute("aria-pressed")).toBe("false");
    expect(within(screen.getByTestId("chain-lower-pane")).getByTestId("mobile-chain-call-105")).toBe(call);
    expect(within(screen.getByTestId("chain-spot-bar")).getByText(/Spot moved/)).toBeTruthy();
  });

  it("keeps the spot bar mounted through loading and unavailable data", () => {
    const initial = props();
    const { rerender } = render(
      <MobileChainLadder {...initial} visibleStrikes={[]} currentPrice={null} loading />,
    );
    const spotBar = screen.getByTestId("chain-spot-bar");
    expect(screen.getByTestId("mobile-chain-loading")).toBeTruthy();
    expect(screen.getByRole("button", { name: /recenter/i }).hasAttribute("disabled")).toBe(true);

    rerender(<MobileChainLadder {...initial} currentPrice={null} />);
    expect(screen.getByTestId("chain-spot-bar")).toBe(spotBar);
    expect(within(screen.getByTestId("chain-upper-pane")).queryAllByTestId(/mobile-chain-row-/)).toHaveLength(0);
    expect(within(screen.getByTestId("chain-lower-pane")).queryAllByTestId(/mobile-chain-row-/)).toHaveLength(4);
  });

  it("preserves both pane offsets and row membership when spot crosses a strike", () => {
    const initial = props();
    const { rerender } = render(<MobileChainLadder {...initial} />);
    const upper = screen.getByRole("region", { name: "Lower strikes" });
    const lower = screen.getByRole("region", { name: "Higher strikes" });
    const boundaryRow = within(lower).getByTestId("mobile-chain-row-105");
    upper.scrollTop = 45;
    lower.scrollTop = 80;
    fireEvent.touchStart(lower);

    rerender(<MobileChainLadder {...initial} currentPrice={108} atmStrike={110} />);

    expect(upper.scrollTop).toBe(45);
    expect(lower.scrollTop).toBe(80);
    expect(within(lower).getByTestId("mobile-chain-row-105")).toBe(boundaryRow);
    expect(within(upper).queryByTestId("mobile-chain-row-105")).toBeNull();
    expect(screen.getByTestId("mobile-chain-ladder").contains(upper)).toBe(true);
    expect(upper.getAttribute("tabindex")).toBe("0");
    expect(lower.getAttribute("tabindex")).toBe("0");
  });

  it("repartitions and resets both panes only when Recenter is requested", () => {
    const initial = props();
    const { rerender } = render(<MobileChainLadder {...initial} />);
    const upper = screen.getByTestId("chain-upper-pane");
    const lower = screen.getByTestId("chain-lower-pane");
    Object.defineProperty(upper, "scrollHeight", { configurable: true, value: 600 });
    upper.scrollTop = 30;
    lower.scrollTop = 80;
    fireEvent.wheel(upper);
    rerender(<MobileChainLadder {...initial} currentPrice={108} atmStrike={110} />);

    fireEvent.click(screen.getByRole("button", { name: /recenter/i }));

    expect(within(upper).getByTestId("mobile-chain-row-105")).toBeTruthy();
    expect(within(lower).queryByTestId("mobile-chain-row-105")).toBeNull();
    expect(upper.scrollTop).toBe(600);
    expect(lower.scrollTop).toBe(0);
  });

  it("honors the shared parent anchor and forwards browsing and recenter actions", () => {
    const initial = props();
    const onBrowse = vi.fn();
    const onRecenter = vi.fn();
    const { rerender } = render(
      <MobileChainLadder
        {...initial}
        currentPrice={108}
        anchorPrice={102}
        anchorRevision="SPY:1"
        onBrowse={onBrowse}
        onRecenter={onRecenter}
      />,
    );
    const upper = screen.getByTestId("chain-upper-pane");
    const lower = screen.getByTestId("chain-lower-pane");
    fireEvent.keyDown(lower, { key: "PageDown" });
    fireEvent.click(screen.getByRole("button", { name: /recenter/i }));
    expect(onBrowse).toHaveBeenCalledTimes(1);
    expect(onRecenter).toHaveBeenCalledTimes(1);
    expect(within(lower).getByTestId("mobile-chain-row-105")).toBeTruthy();

    Object.defineProperty(upper, "scrollHeight", { configurable: true, value: 600 });
    lower.scrollTop = 80;
    rerender(
      <MobileChainLadder
        {...initial}
        currentPrice={108}
        anchorPrice={108}
        anchorRevision="SPY:2"
        onBrowse={onBrowse}
        onRecenter={onRecenter}
      />,
    );
    expect(within(upper).getByTestId("mobile-chain-row-105")).toBeTruthy();
    expect(upper.scrollTop).toBe(600);
    expect(lower.scrollTop).toBe(0);
  });

  it("positions newly loaded cached strikes even when the parent anchor revision is unchanged", () => {
    const initial = props();
    const { rerender } = render(
      <MobileChainLadder {...initial} anchorPrice={102} anchorRevision="SPY:1" />,
    );
    const upper = screen.getByTestId("chain-upper-pane");
    const lower = screen.getByTestId("chain-lower-pane");
    Object.defineProperty(upper, "scrollHeight", { configurable: true, value: 800 });
    upper.scrollTop = 30;
    lower.scrollTop = 80;
    const cachedStrikes = [85, 90, 95, 100, 105, 110].map((strike) => ({
      strike,
      callKey: optionKey({ symbol: "SPY", expiry: EXPIRY, strike, right: "C" }),
      putKey: optionKey({ symbol: "SPY", expiry: EXPIRY, strike, right: "P" }),
    }));

    rerender(
      <MobileChainLadder {...initial} visibleStrikes={cachedStrikes} anchorPrice={102} anchorRevision="SPY:1" />,
    );

    expect(upper.scrollTop).toBe(800);
    expect(lower.scrollTop).toBe(0);
    expect(within(upper).getByTestId("mobile-chain-row-85")).toBeTruthy();

    upper.scrollTop = 45;
    lower.scrollTop = 80;
    fireEvent.touchStart(lower);
    rerender(
      <MobileChainLadder
        {...initial}
        visibleStrikes={cachedStrikes.map((row) => ({ ...row }))}
        currentPrice={108}
        anchorPrice={102}
        anchorRevision="SPY:1"
      />,
    );

    expect(upper.scrollTop).toBe(45);
    expect(lower.scrollTop).toBe(80);
  });

  it("keeps side filtering and contract selection available inside either pane", () => {
    const onAddLeg = vi.fn();
    render(<MobileChainLadder {...props()} sideFilter="puts" onAddLeg={onAddLeg} />);
    expect(screen.queryByTestId("mobile-chain-call-100")).toBeNull();
    fireEvent.click(within(screen.getByTestId("chain-upper-pane")).getByTestId("mobile-chain-put-100"));
    fireEvent.click(screen.getByTestId("mobile-chain-detail-sell"));
    expect(onAddLeg).toHaveBeenCalledWith(100, "P", "SELL");
    fireEvent.click(within(screen.getByTestId("chain-lower-pane")).getByTestId("mobile-chain-put-105"));
    fireEvent.click(screen.getByTestId("mobile-chain-detail-buy"));
    expect(onAddLeg).toHaveBeenLastCalledWith(105, "P", "BUY");
  });
});
