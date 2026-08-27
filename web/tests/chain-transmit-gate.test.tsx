// @vitest-environment jsdom
//
// Exploration 1a's transmit gate. Two deliberate safety properties:
//
//   1. Step 1 VERIFIES, it does not send. Transmit only follows review.
//   2. An order with UNBOUNDED max loss cannot be transmitted until the
//      operator acknowledges that, with the actual loss figures in front of
//      them. Bounded orders need no acknowledgement.
//
// This only ever ADDS a condition. OrderRiskGate remains the chokepoint that
// decides whether an order may be submitted at all; the acknowledgement sits
// on top of it and can never loosen it.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

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

import { chainFetch, chainWithLegs, clickCallCell } from "./helpers/chainHarness";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  searchParamsString = "";
  fetchMock.mockReset();
  fetchMock.mockImplementation((input) => chainFetch(input as RequestInfo | URL));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setLimitPrice(builder: HTMLElement, value: string) {
  const inputs = builder.querySelectorAll<HTMLInputElement>("input[type='number']");
  // First is quantity, second is the limit price.
  fireEvent.change(inputs[1], { target: { value } });
}

/** Stage a naked short call (unbounded) and price it. */
async function shortCallTicket() {
  const builder = await chainWithLegs(() => clickCallCell(970, 0));
  setLimitPrice(builder, "2.98");
  await waitFor(() => expect(builder.querySelector(".ticket-risk")).toBeTruthy());
  return builder;
}

function verifyButton(): HTMLButtonElement {
  return screen.getByTestId("ticket-verify") as HTMLButtonElement;
}

function transmitButton(): HTMLButtonElement {
  return screen.getByTestId("ticket-transmit") as HTMLButtonElement;
}

describe("ticket transmit gate", () => {
  it("labels step 1 as a verify, not a send", async () => {
    const builder = await shortCallTicket();
    const verify = verifyButton();
    expect(verify.textContent?.toUpperCase()).toContain("VERIFY");
    expect(builder.textContent?.toUpperCase()).toContain("STEP 1 OF 2");
    // Nothing may be transmitted from step 1.
    expect(screen.queryByTestId("ticket-transmit")).toBeNull();
  });

  it("holds transmit until unbounded risk is acknowledged", async () => {
    await shortCallTicket();
    fireEvent.click(verifyButton());

    const ack = await screen.findByTestId("ticket-unbounded-ack");
    expect((ack as HTMLInputElement).checked).toBe(false);

    const transmit = transmitButton();
    expect(transmit.disabled).toBe(true);
    expect(transmit.textContent?.toUpperCase()).toContain("AWAITING ACKNOWLEDGEMENT");
  });

  it("states the actual loss the operator is acknowledging", async () => {
    await shortCallTicket();
    fireEvent.click(verifyButton());
    const block = await screen.findByTestId("ticket-unbounded-warning");
    // Real figures from the payoff, not boilerplate: a 970 call sold at 2.98
    // turns loss-making above 972.98.
    expect(block.textContent).toContain("972.98");
    expect(block.textContent?.toUpperCase()).toContain("UNBOUNDED");
  });

  it("arms transmit once acknowledged", async () => {
    await shortCallTicket();
    fireEvent.click(verifyButton());
    const ack = await screen.findByTestId("ticket-unbounded-ack");
    fireEvent.click(ack);
    await waitFor(() => expect(transmitButton().disabled).toBe(false));
    expect(transmitButton().textContent?.toUpperCase()).not.toContain("AWAITING");
  });

  it("re-arms the gate when the operator goes back and changes the order", async () => {
    await shortCallTicket();
    fireEvent.click(verifyButton());
    fireEvent.click(await screen.findByTestId("ticket-unbounded-ack"));
    await waitFor(() => expect(transmitButton().disabled).toBe(false));

    // Back to compose, then verify again: the acknowledgement must not persist.
    fireEvent.click(screen.getByTestId("ticket-back"));
    await waitFor(() => expect(screen.queryByTestId("ticket-transmit")).toBeNull());
    fireEvent.click(verifyButton());
    const ack = await screen.findByTestId("ticket-unbounded-ack");
    expect((ack as HTMLInputElement).checked).toBe(false);
    expect(transmitButton().disabled).toBe(true);
  });

  // Arming the button is not the same as being able to send. The submit
  // handler re-checks the acknowledgement, so it must see the CURRENT one —
  // a handler memoised without it stays closed over `false` forever and the
  // armed button silently does nothing (production 2026-08-27).
  it("transmits the order once acknowledged", async () => {
    await shortCallTicket();
    fireEvent.click(verifyButton());
    fireEvent.click(await screen.findByTestId("ticket-unbounded-ack"));
    await waitFor(() => expect(transmitButton().disabled).toBe(false));

    fireEvent.click(transmitButton());

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).includes("/api/orders/place")),
      ).toBe(true),
    );
  });
});
