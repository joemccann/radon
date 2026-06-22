// @vitest-environment jsdom
//
// Component render test for the asset cockpit IA overhaul.
//
// Renders the real <AssetCockpit> (web/components/ticker-detail/AssetCockpit.tsx)
// against the landed prop contract. The assertions encode the LOCKED IA decisions:
//   (a) the Book region renders and is NOT inside the slide-over deck
//   (b) the act column is ticket-focused: the Order ticket is docked; the full
//       position card grid is NOT docked — position detail lives in the p-deck
//   (c) the header shows last / netΔ / spread but NO standalone bid×ask scalar
//       duplicating the book; the position chip is a link with no P&L number
//   (d) opening a deck shows the deck while the Book region stays present and is
//       not inside / occluded by the deck (book keeps full width)
//   (e) Esc / close calls onDeckChange(null)
//   (f) for a held combo, the full position detail (legs / entry-mark-P&L cards)
//       is reachable via the p-deck, not docked in the act column
//
// SELECTOR NOTE: the landed components select on class names, not data-testid
// (`.cockpit-head`, `.book-region`, `.act-ticket`, `.act-position`, `.glyph-rail`,
// `.asset-deck`, `.asset-deck-x`). These tests assert on those robust structural
// selectors + rendered text. A future hardening pass SHOULD add stable
// data-testid hooks (cockpit-header / cockpit-book / cockpit-ticket /
// cockpit-position / asset-deck) so the tests are not coupled to layout classes.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import AssetCockpit, { type AssetCockpitProps } from "../components/ticker-detail/AssetCockpit";
import { OrderActionsProvider } from "../lib/OrderActionsContext";
import type { PortfolioPosition } from "../lib/types";

// next/navigation is pulled in transitively by OrderTab / OptionsChainTab etc.;
// mock it so the jsdom render never reaches for a real router.
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

/* ── fixtures ── */

// quotePriceData is the single source for header last/netΔ/spread. last=142.18,
// close=140.90 → netΔ ≈ +0.91%; bid/ask present so the header can derive a spread
// label, but it must NOT print a bid×ask scalar — the book owns depth.
const QUOTE_PRICE_DATA = {
  last: 142.18,
  close: 140.9,
  bid: 142.18,
  ask: 142.2,
} as unknown as AssetCockpitProps["quotePriceData"];

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

const DEPTHS = { MU: STOCK_BOOK } as unknown as AssetCockpitProps["depths"];
const TAPE = { MU: [{ price: 142.18, size: 1, exchange: "FINY", time: "1780000027" }] } as unknown as AssetCockpitProps["tape"];

// A held combo (5x risk reversal). Net mark is a CREDIT whose only authoritative
// home is the Position area (never the header).
const COMBO_POSITION: PortfolioPosition = {
  id: 42,
  ticker: "MU",
  structure: "COMBO 5X RR",
  structure_type: "Risk Reversal",
  risk_profile: "undefined",
  expiry: "20260620",
  contracts: 5,
  direction: "long",
  entry_cost: -4080,
  max_risk: null,
  market_value: -4080,
  // resolveEntryCost (multi-leg) = Σ sign·|entry_cost| = -6200 + 2120 = -4080 →
  // fmtUsd → "-$4,080"; a clean grouped-thousands net that only the Position
  // panel should print.
  legs: [
    {
      direction: "SHORT",
      type: "Call",
      strike: 1000,
      contracts: 5,
      entry_cost: 6200,
      avg_cost: 12.4,
      market_price: 12.4,
      market_value: 6200,
    },
    {
      direction: "LONG",
      type: "Put",
      strike: 865,
      contracts: 5,
      entry_cost: 2120,
      avg_cost: 4.3,
      market_price: 4.3,
      market_value: 2120,
    },
  ] as unknown as PortfolioPosition["legs"],
  ib_daily_pnl: 620,
  kelly_optimal: 0.025,
  target: null,
  stop: null,
  entry_date: "2026-05-01",
};

function baseProps(overrides: Partial<AssetCockpitProps> = {}): AssetCockpitProps {
  return {
    ticker: "MU",
    position: null,
    prices: PRICES,
    fundamentals: {} as AssetCockpitProps["fundamentals"],
    portfolio: { positions: [], account_summary: {} } as unknown as AssetCockpitProps["portfolio"],
    depths: DEPTHS,
    tape: TAPE,
    bookKey: "MU",
    bookKind: "stock",
    quotePriceData: QUOTE_PRICE_DATA,
    priceData: PRICES.MU as AssetCockpitProps["priceData"],
    isSpreadNet: false,
    tickerOrders: [],
    stockFallback: null,
    theme: "dark",
    activeDeck: null,
    onDeckChange: vi.fn(),
    instrumentView: "position",
    canSwitchInstrument: false,
    onInstrumentViewChange: vi.fn(),
    viewUnderlying: false,
    ...overrides,
  };
}

function renderCockpit(overrides: Partial<AssetCockpitProps> = {}) {
  const onDeckChange = vi.fn(overrides.onDeckChange);
  const props = baseProps({ ...overrides, onDeckChange });
  const result = render(
    <OrderActionsProvider>
      <AssetCockpit {...props} />
    </OrderActionsProvider>,
  );
  return { ...result, onDeckChange, props };
}

const bookRegion = (c: HTMLElement) => c.querySelector(".book-region") as HTMLElement | null;
const header = (c: HTMLElement) => c.querySelector(".cockpit-head") as HTMLElement | null;
const deck = (c: HTMLElement) => c.querySelector(".asset-deck") as HTMLElement | null;

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe("AssetCockpit — book-first, always-docked layout (flat fixture)", () => {
  it("(a) renders the Book region and it is NOT inside the deck", () => {
    const { container } = renderCockpit();
    const book = bookRegion(container);
    expect(book).toBeTruthy();
    // Book renders real content (L2 montage when entitled, else the L1 fallback).
    expect(
      book!.querySelector(".book-sides") ??
        book!.querySelector(".book-ladder") ??
        book!.querySelector(".book-l1"),
    ).toBeTruthy();
    // The book is never inside the slide-over deck.
    const d = deck(container);
    if (d) expect(d.contains(book!)).toBe(false);
    expect(book!.closest(".asset-deck")).toBeNull();
  });

  it("(b) mounts the Order ticket AND the Position slot simultaneously (no tab switch)", () => {
    const { container } = renderCockpit();
    expect(container.querySelector(".act-ticket")).toBeTruthy();
    expect(container.querySelector(".act-position")).toBeTruthy();
  });

  it("(c) header shows last / netΔ / spread but NO standalone bid×ask scalar duplicating the book", () => {
    const { container } = renderCockpit();
    const h = header(container)!;
    const text = h.textContent ?? "";
    expect(text).toContain("142.18"); // last
    expect(text.toUpperCase()).toContain("SPREAD"); // derived spread label
    expect(text).toMatch(/0\.91/); // netΔ%
    // The book owns bid/ask depth; the header derives a spread but must not print
    // a bid×ask scalar. The ask price (142.20) is exclusive to the book and must
    // not leak into the header.
    expect(text).not.toContain("142.20");
    expect(text.toLowerCase()).not.toMatch(/bid\s*[x×]\s*ask/);
  });

  it("(c) the flat position chip is a link/button with no P&L number, and opens the position deck", () => {
    const { container, onDeckChange } = renderCockpit({ position: null });
    const h = header(container)!;
    const chip = h.querySelector(".ckh-poschip") as HTMLElement;
    expect(chip).toBeTruthy();
    expect(chip.textContent ?? "").toContain("FLAT");
    // No dollar / P&L figure on the chip.
    expect(chip.textContent ?? "").not.toMatch(/\$\s?-?\d/);
    // It links into the position deck.
    fireEvent.click(chip);
    expect(onDeckChange).toHaveBeenCalledWith("p");
  });
});

describe("AssetCockpit — deck open never occludes the book (flat fixture)", () => {
  it("(d) opening a deck via the glyph rail shows the deck and the Book region stays present + outside the deck", () => {
    const { container, rerender, onDeckChange, props } = renderCockpit({ activeDeck: null });

    // Open the chain deck via the glyph rail (the `c` glyph).
    const rail = container.querySelector(".glyph-rail")!;
    const chainGlyph = [...rail.querySelectorAll(".glyph")].find(
      (g) => g.querySelector(".glyph-k")?.textContent === "c",
    ) as HTMLElement;
    expect(chainGlyph).toBeTruthy();
    fireEvent.click(chainGlyph);
    expect(onDeckChange).toHaveBeenCalledWith("c");

    // Re-render in an opened state (parent owns activeDeck). Use the `:` command
    // palette deck — it renders provider-free static content, so this test stays
    // focused on the layout invariant (deck open ⇒ book not occluded) without
    // pulling the chain's TickerDetailProvider into scope.
    rerender(
      <OrderActionsProvider>
        <AssetCockpit {...{ ...props, activeDeck: ":" as typeof props.activeDeck }} />
      </OrderActionsProvider>,
    );

    const d = deck(container)!;
    expect(d).toBeTruthy();
    expect(d.className).toContain("open");

    // The book is still in the DOM, NOT inside the deck, and still renders content.
    const book = bookRegion(container)!;
    expect(book).toBeTruthy();
    expect(d.contains(book)).toBe(false);
    expect(book.closest(".asset-deck")).toBeNull();
    expect(
      book.querySelector(".book-sides") ??
        book.querySelector(".book-ladder") ??
        book.querySelector(".book-l1"),
    ).toBeTruthy();
  });

  it("(e) the deck close button calls onDeckChange(null)", () => {
    // `:` (command palette) deck renders provider-free static content.
    const { container, onDeckChange } = renderCockpit({ activeDeck: ":" });
    const closeBtn = container.querySelector(".asset-deck-x") as HTMLElement;
    expect(closeBtn).toBeTruthy();
    fireEvent.click(closeBtn);
    expect(onDeckChange).toHaveBeenCalledWith(null);
  });

  it("(e) Esc (no input focused) calls onDeckChange(null) when a deck is open", () => {
    const { onDeckChange } = renderCockpit({ activeDeck: ":" });
    document.body.focus();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onDeckChange).toHaveBeenCalledWith(null);
  });
});

describe("AssetCockpit — held combo fixture", () => {
  it("(b) act column is ticket-focused: ticket docked, full position card grid NOT docked", () => {
    const { container } = renderCockpit({
      position: COMBO_POSITION,
      isSpreadNet: true,
    });
    // The ticket is docked at the top of the act column.
    expect(container.querySelector(".act-ticket")).toBeTruthy();

    // The act column stays clean — no multi-card position grid docked. The full
    // PositionTab detail belongs to the p-deck, not the dock.
    const positionArea = container.querySelector(".act-position") as HTMLElement;
    expect(positionArea).toBeTruthy();
    expect(positionArea.querySelector(".position-summary-grid")).toBeNull();
    expect(positionArea.querySelector(".pos-stat")).toBeNull();

    const h = header(container)!;
    const chip = h.querySelector(".ckh-poschip") as HTMLElement;
    // Chip names the structure and links — but carries no dollar figure.
    expect(chip.textContent ?? "").toContain("COMBO 5X RR");
    expect(chip.textContent ?? "").not.toMatch(/\$\s?-?\d/);
  });

  it("(b) the docked held cue is a single one-line summary button that opens the p-deck", () => {
    const { container, onDeckChange } = renderCockpit({
      position: COMBO_POSITION,
      isSpreadNet: true,
    });
    const positionArea = container.querySelector(".act-position") as HTMLElement;
    // One-line summary names the structure and links into the position deck.
    const summary = positionArea.querySelector("button") as HTMLElement;
    expect(summary).toBeTruthy();
    expect(summary.textContent ?? "").toContain("COMBO 5X RR");
    fireEvent.click(summary);
    expect(onDeckChange).toHaveBeenCalledWith("p");
  });

  it("(f) the full position detail (legs / entry-mark-P&L cards) lives in the p-deck, not the act dock", () => {
    const { container } = renderCockpit({
      position: COMBO_POSITION,
      isSpreadNet: true,
      activeDeck: "p",
    });

    // The full PositionTab card grid renders inside the open p-deck...
    const d = deck(container)!;
    expect(d).toBeTruthy();
    expect(d.querySelector(".position-summary-grid")).toBeTruthy();

    // ...and is NOT docked in the act column.
    const positionArea = container.querySelector(".act-position") as HTMLElement;
    expect(positionArea.querySelector(".position-summary-grid")).toBeNull();

    // The combo's net dollar figure is never restated in the header (single-home
    // invariant); the header carries no large grouped-thousands dollar amount.
    const h = header(container)!;
    expect((h.textContent ?? "").toUpperCase()).toContain("NET");
    expect(h.textContent ?? "").not.toMatch(/\$\d{1,3},\d{3}/);
  });
});

describe("instrument switch (held option ⟷ underlying stock)", () => {
  it("shows the static kind chip and no switcher when not switchable", () => {
    const { container } = renderCockpit({ canSwitchInstrument: false, bookKind: "option" });
    expect(container.querySelector(".ckh-instr")).toBeNull();
    expect(container.querySelector(".ckh-kind")?.textContent).toBe("OPTION");
  });

  it("renders a STOCK|OPTION toggle when switchable, with the held view active", () => {
    const { container } = renderCockpit({
      canSwitchInstrument: true,
      instrumentView: "position",
      bookKind: "option",
    });
    const segs = [...container.querySelectorAll(".ckh-instr-seg")];
    expect(segs.map((s) => s.textContent?.trim())).toEqual(["STOCK", "OPTION"]);
    expect(container.querySelector(".ckh-instr-seg.on")?.textContent?.trim()).toBe("OPTION");
  });

  it("clicking STOCK requests the underlying view", () => {
    const onInstrumentViewChange = vi.fn();
    const { container } = renderCockpit({
      canSwitchInstrument: true,
      instrumentView: "position",
      bookKind: "option",
      onInstrumentViewChange,
    });
    const stockSeg = container.querySelector(".ckh-instr-seg") as HTMLElement; // first = STOCK
    fireEvent.click(stockSeg);
    expect(onInstrumentViewChange).toHaveBeenCalledWith("underlying");
  });
});
