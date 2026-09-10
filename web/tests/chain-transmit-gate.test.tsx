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
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

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

import { EXPIRY, chainFetch, chainWithLegs, clickCallCell } from "./helpers/chainHarness";

const fetchMock = vi.fn<typeof fetch>();

/**
 * Every POST the chain makes, recorded whole. The gate is only proven at the
 * wire: url, method and the parsed body, not a substring of the url.
 */
type SentRequest = { url: string; method: string; body: string };
const sent: SentRequest[] = [];
/** Answer the next POST with a rejection, as IB would on a bad order. */
let rejectNextPlace = false;

beforeEach(() => {
  searchParamsString = "";
  sent.length = 0;
  rejectNextPlace = false;
  fetchMock.mockReset();
  fetchMock.mockImplementation((input, init) => {
    const method = (init as RequestInit | undefined)?.method ?? "GET";
    if (method === "POST") {
      sent.push({ url: String(input), method, body: String((init as RequestInit).body) });
      if (rejectNextPlace) {
        rejectNextPlace = false;
        return Promise.resolve(
          new Response(JSON.stringify({ error: "rejected" }), {
            status: 400,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
    }
    return chainFetch(input as RequestInfo | URL);
  });
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

/**
 * Two short calls at different strikes and different sizes: still unbounded,
 * so the gate engages, but now the POST goes down the COMBO branch — the only
 * one that carries the signed net `limitPrice` and per-leg `ratio`.
 */
async function shortCallComboTicket() {
  const builder = await chainWithLegs(() => {
    clickCallCell(970, 0);
    clickCallCell(960, 0);
  });
  const numberInputs = () => builder.querySelectorAll<HTMLInputElement>("input[type='number']");
  // [leg 1 qty, leg 1 limit, leg 2 qty, leg 2 limit, net limit].
  fireEvent.change(numberInputs()[0], { target: { value: "2" } });
  fireEvent.change(numberInputs()[1], { target: { value: "12.50" } });
  fireEvent.change(numberInputs()[3], { target: { value: "18.00" } });
  const inputs = numberInputs();
  fireEvent.change(inputs[inputs.length - 1], { target: { value: "-30.50" } });
  await waitFor(() => expect(builder.querySelector(".ticket-risk")).toBeTruthy());
  return builder;
}

/**
 * The React click handler behind a node, reached past its `disabled`
 * attribute. `disabled` is UI; the property under test is whether the handler
 * itself refuses to reach the wire, so the test calls what the button calls.
 */
function reactOnClick(node: HTMLElement): () => void {
  const key = Object.keys(node).find((k) => k.startsWith("__reactProps$"));
  if (!key) throw new Error("no React props on node");
  const props = (node as unknown as Record<string, { onClick?: () => void }>)[key];
  if (typeof props.onClick !== "function") throw new Error("node has no onClick");
  return props.onClick;
}

async function acknowledgeAndArm() {
  fireEvent.click(verifyButton());
  fireEvent.click(await screen.findByTestId("ticket-unbounded-ack"));
  await waitFor(() => expect(transmitButton().disabled).toBe(false));
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
  it("puts nothing on the wire while the acknowledgement is outstanding", async () => {
    await shortCallTicket();
    fireEvent.click(verifyButton());
    const ack = (await screen.findByTestId("ticket-unbounded-ack")) as HTMLInputElement;
    expect(ack.checked).toBe(false);

    // Past `disabled`: the handler, not the button, has to refuse.
    await act(async () => {
      reactOnClick(transmitButton())();
    });

    expect(sent).toHaveLength(0);
  });

  it("transmits the exact single-leg order once acknowledged", async () => {
    await shortCallTicket();
    await acknowledgeAndArm();

    // Twice in one tick: `loading` is state and invisible to the second call,
    // so only the in-flight ref stops the double-send.
    await act(async () => {
      const transmit = reactOnClick(transmitButton());
      transmit();
      transmit();
    });

    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe("/api/orders/place");
    expect(sent[0].method).toBe("POST");
    const body = JSON.parse(sent[0].body);
    expect(body.type).toBe("option");
    expect(body.symbol).toBe("MU");
    expect(body.action).toBe("SELL");
    expect(body.quantity).toBe(1);
    expect(body.limitPrice).toBe(2.98);
    expect(body.tif).toBe("DAY");
    expect(body.expiry).toBe(EXPIRY.compact);
    expect(body.strike).toBe(970);
    expect(body.right).toBe("CALL");
  });

  // The in-flight ref has to be RELEASED as well as taken: a rejected order
  // leaves the rail on the confirm step, and the operator must be able to
  // correct and resend. A ref set but never reset in `finally` bricks that.
  it("releases the in-flight lock so a rejected order can be resent", async () => {
    await shortCallTicket();
    await acknowledgeAndArm();
    rejectNextPlace = true;

    await act(async () => {
      fireEvent.click(transmitButton());
    });
    expect(sent).toHaveLength(1);

    await act(async () => {
      fireEvent.click(transmitButton());
    });
    expect(sent).toHaveLength(2);
  });

  // The combo branch is the one that carries the SIGNED net limit price. A
  // credit transmitted as a debit is a different order at the same magnitude,
  // so the sign is asserted, not `Math.abs`.
  it("transmits the exact combo order, signed limit price and leg ratios included", async () => {
    await shortCallComboTicket();
    await acknowledgeAndArm();

    await act(async () => {
      fireEvent.click(transmitButton());
    });

    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe("/api/orders/place");
    expect(sent[0].method).toBe("POST");
    const body = JSON.parse(sent[0].body);
    expect(body.type).toBe("combo");
    expect(body.symbol).toBe("MU");
    expect(body.action).toBe("BUY");
    expect(body.quantity).toBe(1);
    // A net CREDIT of 30.50. Negating this sign ships a debit.
    expect(body.limitPrice).toBe(-30.5);
    expect(body.tif).toBe("DAY");
    expect(body.legs).toEqual([
      {
        symbol: "MU",
        secType: "OPT",
        expiry: EXPIRY.compact,
        strike: 970,
        right: "CALL",
        action: "SELL",
        ratio: 2,
        limitPrice: 12.5,
      },
      {
        symbol: "MU",
        secType: "OPT",
        expiry: EXPIRY.compact,
        strike: 960,
        right: "CALL",
        action: "SELL",
        ratio: 1,
        limitPrice: 18,
      },
    ]);
  });
});
