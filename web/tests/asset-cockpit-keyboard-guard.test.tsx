// @vitest-environment jsdom
//
// Keyboard-guard test for the asset cockpit single-key deck shortcuts.
//
// Renders the real <AssetCockpit>; the keyboard handler lives in <AssetDeck> with
// an activeElement guard (plan §3a / §8: "single-key deck opens must not fire while
// the user types in ticket Qty/Limit/TIF").
//
// Invariant: a bare keydown of a deck key (c/p/n/r/s/i) opens the matching deck
// ONLY when focus is not inside a text input/textarea/select/contenteditable.
// When an order-ticket input is focused, the shortcut is suppressed so typing "c"
// into Qty/Limit does not slam a deck open over the Act column.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import AssetCockpit, { type AssetCockpitProps } from "../components/ticker-detail/AssetCockpit";
import { OrderActionsProvider } from "../lib/OrderActionsContext";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/MU",
  useRouter: () => ({
    replace: vi.fn(),
    push: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));

const PRICES = {
  MU: { ticker: "MU", last: 142.18, bid: 142.18, ask: 142.2, close: 140.9 },
} as unknown as AssetCockpitProps["prices"];

const STOCK_BOOK = {
  symbol: "MU",
  kind: "stock",
  isSmartDepth: true,
  feed: "SMART DEPTH",
  entitled: true,
  timestamp: "2026-05-31T16:00:00Z",
  bid: [{ price: 142.18, size: 831, marketMaker: "NSDQ", exchange: "SMART" }],
  ask: [{ price: 142.2, size: 40, marketMaker: "ARCA", exchange: "SMART" }],
} as unknown as NonNullable<AssetCockpitProps["depths"]>[string];

function renderCockpit() {
  const onDeckChange = vi.fn();
  const props: AssetCockpitProps = {
    ticker: "MU",
    position: null,
    prices: PRICES,
    fundamentals: {} as AssetCockpitProps["fundamentals"],
    portfolio: { positions: [], account_summary: {} } as unknown as AssetCockpitProps["portfolio"],
    depths: { MU: STOCK_BOOK } as unknown as AssetCockpitProps["depths"],
    tape: {} as AssetCockpitProps["tape"],
    bookKey: "MU",
    bookKind: "stock",
    quotePriceData: { last: 142.18, close: 140.9, bid: 142.18, ask: 142.2 } as unknown as AssetCockpitProps["quotePriceData"],
    priceData: PRICES.MU as AssetCockpitProps["priceData"],
    isSpreadNet: false,
    tickerOrders: [],
    stockFallback: null,
    theme: "dark",
    activeDeck: null,
    onDeckChange,
  };
  const result = render(
    <OrderActionsProvider>
      <AssetCockpit {...props} />
    </OrderActionsProvider>,
  );
  return { ...result, onDeckChange };
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe("AssetCockpit — single-key deck shortcut keyboard guard", () => {
  it("does NOT open a deck when a deck key is pressed while focus is in a text input", () => {
    const { container, onDeckChange } = renderCockpit();

    // A focused text input stands in for the order-ticket fields; the guard checks
    // document.activeElement's tag, not a specific node.
    const input = document.createElement("input");
    input.type = "text";
    container.appendChild(input);
    input.focus();
    expect(document.activeElement).toBe(input);

    fireEvent.keyDown(input, { key: "c" });
    fireEvent.keyDown(input, { key: "p" });
    fireEvent.keyDown(input, { key: "n" });

    expect(onDeckChange).not.toHaveBeenCalled();
  });

  it("DOES open the matching deck when the same key is pressed with no input focused", () => {
    const { onDeckChange } = renderCockpit();

    document.body.focus();
    fireEvent.keyDown(document.body, { key: "c" });

    expect(onDeckChange).toHaveBeenCalledWith("c");
  });
});
