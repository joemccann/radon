// @vitest-environment jsdom
//
// Turn 2 of the canvas: the docked rail becomes a bottom sheet on mobile. The
// information hierarchy and the safety gate must survive the rotation - same
// legs -> price -> risk -> gate -> CTA order, same two-step verify, and the
// same refusal to transmit unbounded risk without an explicit acknowledgement.
//
// A gate that exists on desktop but not on the phone is worse than no gate,
// because the operator learns to trust it.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import MobileOrderTicket from "@/components/mobile/MobileOrderTicket";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioData } from "@/lib/types";

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({ pushNotification: vi.fn() }),
  useOrderActionsOptional: () => ({ pushNotification: vi.fn() }),
}));

const EXPIRY = (() => {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + 21);
  return d.toISOString().slice(0, 10);
})();
const COMPACT = EXPIRY.replaceAll("-", "");
const CALL_KEY = `MU_${COMPACT}_970_C`;

function quote(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol, last: (bid + ask) / 2, lastIsCalculated: false, bid, ask,
    bidSize: 10, askSize: 10, volume: 100, high: ask, low: bid, open: bid, close: bid,
    week52High: null, week52Low: null, avgVolume: null, delta: null, gamma: null,
    theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
  } as PriceData;
}

const PRICES: Record<string, PriceData> = {
  MU: quote("MU", 967.5, 968.0),
  [CALL_KEY]: quote(CALL_KEY, 2.9, 3.06),
};

const PORTFOLIO = {
  bankroll: 100_000, peak_value: 100_000, last_sync: new Date().toISOString(),
  total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
  position_count: 0, defined_risk_count: 0, undefined_risk_count: 0,
  avg_kelly_optimal: null, positions: [],
} as unknown as PortfolioData;

/** A naked short call: unbounded max loss, so the gate must engage. */
const SHORT_CALL = [
  {
    id: "leg-1",
    action: "SELL" as const,
    right: "C" as const,
    strike: 970,
    expiry: COMPACT,
    quantity: 1,
    limitPrice: 2.98,
  },
];

function renderTicket() {
  return render(
    <MobileOrderTicket
      open
      ticker="MU"
      legs={SHORT_CALL}
      prices={PRICES}
      portfolio={PORTFOLIO}
      onClose={vi.fn()}
      onPlaced={vi.fn()}
    />,
  );
}

const fetchMock = vi.fn(() =>
  Promise.resolve(new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } })),
);

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/**
 * The React click handler behind a node, reached past its `disabled`
 * attribute. `disabled` is UI; the finding is whether the handler itself
 * refuses, so the test has to call what the button would call.
 */
function reactOnClick(node: HTMLElement): () => void {
  const key = Object.keys(node).find((k) => k.startsWith("__reactProps$"));
  if (!key) throw new Error("no React props on node");
  const props = (node as unknown as Record<string, { onClick?: () => void }>)[key];
  if (typeof props.onClick !== "function") throw new Error("node has no onClick");
  return props.onClick;
}

function placeCalls() {
  return fetchMock.mock.calls.filter(([url]) => String(url).includes("/api/orders/place"));
}

describe("mobile ticket transmit gate", () => {
  it("shows the same nine-cell risk block the desktop rail shows", async () => {
    renderTicket();
    await waitFor(() => expect(document.querySelector(".ticket-risk")).toBeTruthy());
    const labels = [...document.querySelectorAll(".ticket-risk-cell-label")].map((n) => n.textContent);
    expect(labels).toEqual(
      expect.arrayContaining(["MAX GAIN", "MAX LOSS", "BREAKEVENS", "MARGIN REQ"]),
    );
  });

  it("holds send until unbounded risk is acknowledged", async () => {
    renderTicket();
    fireEvent.click(await screen.findByTestId("mobile-order-ticket-review"));
    const ack = await screen.findByTestId("ticket-unbounded-ack");
    expect((ack as HTMLInputElement).checked).toBe(false);
    const submit = screen.getByTestId("mobile-order-ticket-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });

  it("arms send once acknowledged", async () => {
    renderTicket();
    fireEvent.click(await screen.findByTestId("mobile-order-ticket-review"));
    fireEvent.click(await screen.findByTestId("ticket-unbounded-ack"));
    await waitFor(() => {
      const submit = screen.getByTestId("mobile-order-ticket-submit") as HTMLButtonElement;
      expect(submit.disabled).toBe(false);
    });
  });

  it("never POSTs an unacknowledged unbounded order, disabled attribute aside", async () => {
    renderTicket();
    fireEvent.click(await screen.findByTestId("mobile-order-ticket-review"));
    const ack = (await screen.findByTestId("ticket-unbounded-ack")) as HTMLInputElement;
    expect(ack.checked).toBe(false);

    const submit = screen.getByTestId("mobile-order-ticket-submit");
    await act(async () => {
      reactOnClick(submit)();
    });

    expect(placeCalls()).toEqual([]);
  });

  it("still transmits once the acknowledgement is given", async () => {
    renderTicket();
    fireEvent.click(await screen.findByTestId("mobile-order-ticket-review"));
    fireEvent.click(await screen.findByTestId("ticket-unbounded-ack"));
    await waitFor(() => {
      expect((screen.getByTestId("mobile-order-ticket-submit") as HTMLButtonElement).disabled).toBe(false);
    });

    await act(async () => {
      reactOnClick(screen.getByTestId("mobile-order-ticket-submit"))();
    });

    await waitFor(() => expect(placeCalls()).toHaveLength(1));
  });

  it("keeps the acknowledgement checkbox at a real tap target size", async () => {
    renderTicket();
    fireEvent.click(await screen.findByTestId("mobile-order-ticket-review"));
    const row = (await screen.findByTestId("ticket-unbounded-ack")).closest("label");
    expect(row?.className).toContain("tap-target");
  });
});
