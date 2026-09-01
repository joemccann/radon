/**
 * @vitest-environment jsdom
 *
 * Layout contract for the mobile order ticket sheet (2026-09-01).
 *
 * The bug: in build view the pinned, non-scrolling `.m-sheet__footer` carried
 * the full TicketRiskBlock (RISK grid + payoff curve) in addition to the
 * teaser and the Clear / Review row. On a 393x852 phone the footer ate most
 * of the 82dvh sheet, crushing the body scroller that owns the size controls
 * (legs, +/- steppers, +5/+10/+25/+50/+100 quick-add chips) into a barely
 * scrollable nested gutter.
 *
 * The rule this test pins:
 *  - ONE sheet scroll. Everything the operator sizes the structure with
 *    (legs, qty steppers, quick-add presets, price, TIF) AND the risk grid +
 *    payoff live in `.m-sheet__body-scroll`, in that order.
 *  - The pinned footer stays compact: status, the two-line teaser, and the
 *    Clear / Review row only. No `.ticket-risk` panel inside the footer.
 *
 * BottomSheet runs for real (portal to document.body), so the assertions
 * query the live sheet DOM rather than component props.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import MobileOrderTicket from "../components/mobile/MobileOrderTicket";
import type { OrderLeg } from "@/lib/optionsChainUtils";
import type { PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

// Unrelated skew analytics; stub so the ticket renders in isolation.
vi.mock("@/components/ComboSkewPanel", () => ({ default: () => null }));

const TICKER = "VIX";
const EXPIRY = "20261020";

function makePrice(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol,
    last: (bid + ask) / 2,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 1,
    askSize: 1,
    volume: 100,
    high: null,
    low: null,
    open: null,
    close: (bid + ask) / 2,
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
  };
}

const PRICES: Record<string, PriceData> = {
  [`${TICKER}_${EXPIRY}_20_C`]: makePrice(`${TICKER}_${EXPIRY}_20_C`, 1.8, 1.96),
  [`${TICKER}_${EXPIRY}_30_C`]: makePrice(`${TICKER}_${EXPIRY}_30_C`, 0.78, 0.86),
};

const EMPTY_PORTFOLIO = { positions: [] } as unknown as PortfolioData;

/** The screenshot repro: a VIX 20/30 bull call spread. */
function bullCallSpread(): OrderLeg[] {
  return [
    {
      id: `${TICKER}_${EXPIRY}_20_C`,
      action: "BUY",
      right: "C",
      strike: 20,
      expiry: EXPIRY,
      quantity: 1,
      limitPrice: null,
    },
    {
      id: `${TICKER}_${EXPIRY}_30_C`,
      action: "SELL",
      right: "C",
      strike: 30,
      expiry: EXPIRY,
      quantity: 1,
      limitPrice: null,
    },
  ];
}

function renderTicket(legs: OrderLeg[]) {
  return render(
    <MobileOrderTicket
      open
      ticker={TICKER}
      legs={legs}
      prices={PRICES}
      spot={21}
      portfolio={EMPTY_PORTFOLIO}
      onClose={() => {}}
      onRemoveLeg={() => {}}
      onUpdateLeg={() => {}}
      onClearLegs={() => {}}
    />,
  );
}

beforeEach(() => {
  global.fetch = vi.fn(() =>
    Promise.resolve({ ok: true, json: async () => ({}) }),
  ) as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("MobileOrderTicket — one sheet scroll owns the size controls", () => {
  it("renders legs, qty presets, and the risk panel inside the body scroller", () => {
    renderTicket(bullCallSpread());

    const body = document.querySelector(".m-sheet__body-scroll");
    expect(body).toBeTruthy();

    // Size controls live in the single sheet scroll.
    const legsBlock = body!.querySelector('[data-testid="mobile-order-ticket-legs"]');
    expect(legsBlock).toBeTruthy();
    expect(
      body!.querySelector(`[data-testid="mobile-order-ticket-leg-${TICKER}_${EXPIRY}_20_C-qty-25"]`),
    ).toBeTruthy();

    // Risk grid + payoff scroll WITH the sheet, below the size controls.
    const risk = body!.querySelector(".ticket-risk");
    expect(risk).toBeTruthy();
    expect(
      legsBlock!.compareDocumentPosition(risk!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("keeps the pinned footer compact: teaser + Clear/Review only, no risk panel", () => {
    renderTicket(bullCallSpread());

    const footer = document.querySelector(".m-sheet__footer");
    expect(footer).toBeTruthy();

    // The payoff/risk instrument must NOT be pinned — it crushed the body
    // scroller to a ~20px nested gutter on 393x852 (2026-09-01 repro).
    expect(footer!.querySelector(".ticket-risk")).toBeNull();
    expect(footer!.querySelector(".mobile-ticket__risk-block")).toBeNull();

    // The thumb-zone keeps the actions and the two-line teaser.
    expect(footer!.querySelector('[data-testid="mobile-order-ticket-clear"]')).toBeTruthy();
    expect(footer!.querySelector('[data-testid="mobile-order-ticket-review"]')).toBeTruthy();
    expect(footer!.querySelector('[data-testid="mobile-order-ticket-teaser"]')).toBeTruthy();
  });

  it("size controls stay in the body scroller for a single-leg ticket too", () => {
    renderTicket([bullCallSpread()[0]]);

    const body = document.querySelector(".m-sheet__body-scroll");
    expect(
      body!.querySelector('[data-testid="mobile-order-ticket-legs"]'),
    ).toBeTruthy();
    const footer = document.querySelector(".m-sheet__footer");
    expect(footer!.querySelector(".ticket-risk")).toBeNull();
  });
});
