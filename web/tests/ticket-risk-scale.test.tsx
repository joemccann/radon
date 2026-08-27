// @vitest-environment jsdom
//
// What scale is the ticket's risk panel on? Two answers were live at once.
//
// The payoff an operator reads before transmitting must not depend on which
// surface they are standing on.
//
// The desktop rail feeds `TicketRiskBlock` ratio-normalised legs, so its
// breakeven describes ONE combo and is a price level independent of how many
// contracts are staged. The phone fed RAW legs, so a 10-lot short 970 call
// showed a breakeven of 970.30 instead of 972.98 - the quantity divided into
// the premium - and the unbounded-risk sentence the operator acknowledges
// carried the same error multiplied by 100 x totalQty on top.
//
// The panel's dollar cells are the opposite case: MAX GAIN, MAX LOSS, MARGIN
// REQ, FUNDS AFTER and TOTAL are all order totals, but the heading read
// "PER 1x COMBO", so the two halves of one panel claimed different scales
// directly above the transmit button.
//
// These tests pin the two surfaces to each other on the same legs, and pin the
// heading to the scale the cells are actually on.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

let searchParamsString = "";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(searchParamsString),
  usePathname: () => "/MU",
  useRouter: () => ({
    replace: vi.fn(), push: vi.fn(), prefetch: vi.fn(),
    back: vi.fn(), forward: vi.fn(), refresh: vi.fn(),
  }),
}));

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));

vi.mock("../components/PriceChart", () => ({
  default: () => React.createElement("div", { "data-testid": "price-chart" }),
}));

vi.mock("@/components/ComboSkewPanel", () => ({ default: () => null }));

import MobileOrderTicket from "@/components/mobile/MobileOrderTicket";
import type { OrderLeg } from "@/lib/optionsChainUtils";
import type { PriceData } from "@/lib/pricesProtocol";
import {
  EXPIRY,
  PORTFOLIO,
  chainFetch,
  chainWithLegs,
  clickCallCell,
  optionQuote,
} from "./helpers/chainHarness";

const CALL_970_KEY = `MU_${EXPIRY.compact}_970_C`;
const PUT_970_KEY = `MU_${EXPIRY.compact}_970_P`;

/** Spot is shared so both surfaces sample the payoff over the same range. */
const SPOT = 967.78;

/** Quoted 2.90 / 3.06 so the ticket's own mid resolves to 2.98. */
const MOBILE_PRICES: Record<string, PriceData> = {
  MU: optionQuote("MU", 967.5, 968.0, { last: SPOT }),
  [CALL_970_KEY]: optionQuote(CALL_970_KEY, 2.9, 3.06),
  [PUT_970_KEY]: optionQuote(PUT_970_KEY, 2.9, 3.06),
};

function shortCallLegs(quantity: number): OrderLeg[] {
  return [
    {
      id: CALL_970_KEY,
      action: "SELL",
      right: "C",
      strike: 970,
      expiry: EXPIRY.compact,
      quantity,
      limitPrice: null,
    },
  ];
}

/** Short straddle: the call side is uncovered, so the gate demands an ack. */
function shortStraddleLegs(quantity: number): OrderLeg[] {
  return [
    ...shortCallLegs(quantity),
    {
      id: PUT_970_KEY,
      action: "SELL",
      right: "P",
      strike: 970,
      expiry: EXPIRY.compact,
      quantity,
      limitPrice: null,
    },
  ];
}

function renderMobile(legs: OrderLeg[]) {
  return render(
    <MobileOrderTicket
      open
      ticker="MU"
      legs={legs}
      prices={MOBILE_PRICES}
      spot={SPOT}
      portfolio={PORTFOLIO}
      onClose={vi.fn()}
      onRemoveLeg={vi.fn()}
      onUpdateLeg={vi.fn()}
      onClearLegs={vi.fn()}
    />,
  );
}

/** Read a labelled cell out of whichever `TicketRiskBlock` is on screen. */
function riskCell(label: string): string {
  const node = [...document.querySelectorAll(".ticket-risk-cell")].find(
    (el) => (el.querySelector(".ticket-risk-cell-label")?.textContent ?? "").trim() === label,
  );
  if (!node) throw new Error(`no risk cell labelled ${label}`);
  return (node.querySelector(".ticket-risk-cell-value")?.textContent ?? "").trim();
}

/** A 970 call sold at 2.98 turns loss-making above 972.98, at any lot size. */
const SHORT_CALL_BREAKEVEN = "972.98";

beforeEach(() => {
  searchParamsString = "";
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => chainFetch(input)),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ticket payoff is per 1x combo on every surface", () => {
  it("mobile prices a 10-lot short call's breakeven at the same level as a 1-lot", async () => {
    renderMobile(shortCallLegs(10));
    await waitFor(() => expect(document.querySelector(".ticket-risk")).toBeTruthy());
    expect(riskCell("BREAKEVENS")).toBe(SHORT_CALL_BREAKEVEN);
  });

  it("mobile states that same breakeven in the unbounded-risk acknowledgement", async () => {
    renderMobile(shortCallLegs(10));
    fireEvent.click(await screen.findByTestId("mobile-order-ticket-review"));
    const block = await screen.findByTestId("ticket-unbounded-warning");
    expect(block.textContent).toContain(SHORT_CALL_BREAKEVEN);
  });

  it("desktop rail reads the same breakeven for the same 10-lot short call", async () => {
    const builder = await chainWithLegs(() => clickCallCell(970, 0));
    const inputs = builder.querySelectorAll<HTMLInputElement>("input[type='number']");
    fireEvent.change(inputs[0], { target: { value: "10" } });
    fireEvent.change(inputs[1], { target: { value: "2.98" } });
    await waitFor(() => expect(builder.querySelector(".ticket-risk")).toBeTruthy());
    expect(riskCell("BREAKEVENS")).toBe(SHORT_CALL_BREAKEVEN);
  });

  it("scales the stock-to-zero loss once, not once per contract", async () => {
    renderMobile(shortStraddleLegs(10));
    fireEvent.click(await screen.findByTestId("mobile-order-ticket-review"));
    const block = await screen.findByTestId("ticket-unbounded-warning");
    // The straddle is a 5.96 credit, so stock-to-zero costs
    // (970 - 5.96) x 100 x 10 contracts. Reading the raw legs multiplied the
    // per-share intrinsic by the lot size a second time and printed 9,694,040.
    expect(block.textContent).toContain("$964,040");
  });
});

/** Stage a 960/970 bull call spread at `quantity` contracts and price it. */
async function bullCallSpread(quantity: number): Promise<HTMLElement> {
  const builder = await chainWithLegs(() => {
    clickCallCell(960, 1);
    clickCallCell(970, 0);
  });
  for (const input of screen.getAllByLabelText("Quantity")) {
    fireEvent.change(input, { target: { value: String(quantity) } });
  }
  fireEvent.change(builder.querySelector<HTMLInputElement>(".modify-price-input")!, {
    target: { value: "6.30" },
  });
  await waitFor(() => expect(builder.querySelector(".ticket-risk")).toBeTruthy());
  return builder;
}

function usd(cellValue: string): number {
  return Number(cellValue.replace(/[^0-9.]/g, ""));
}

describe("the risk panel names the scale its dollars are on", () => {
  it("heads the panel as an order total, not as one combo", async () => {
    await bullCallSpread(10);
    expect(document.querySelector(".ticket-risk-head")?.textContent?.trim()).toBe("RISK \u00b7 ORDER TOTAL");
  });

  it("prices MAX LOSS for every contract staged, not for one", async () => {
    await bullCallSpread(10);
    // A 6.30 debit x 10 contracts x 100, plus the cost model's transaction
    // estimate. Collapsing to one combo reads a few hundred dollars, which is
    // what a ratio-normalised leg quantity would produce.
    const maxLoss = usd(riskCell("MAX LOSS"));
    expect(maxLoss).toBeGreaterThanOrEqual(6_300);
    expect(maxLoss).toBeLessThan(8_000);
  });
});
