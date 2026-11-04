// @vitest-environment jsdom
//
// The mobile ticket arms for the reviewed payload (same machine as the chain
// rail) and does not carry an armed review or a pending close timer across a
// close / reopen of the sheet.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import MobileOrderTicket from "@/components/mobile/MobileOrderTicket";
import type { OrderLeg } from "@/lib/optionsChainUtils";
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
const MOVED: Record<string, PriceData> = {
  MU: quote("MU", 967.5, 968.0),
  [CALL_KEY]: quote(CALL_KEY, 3.5, 3.7),
};

const PORTFOLIO = {
  bankroll: 100_000, peak_value: 100_000, last_sync: new Date().toISOString(),
  total_deployed_pct: 0, total_deployed_dollars: 0, remaining_capacity_pct: 100,
  position_count: 0, defined_risk_count: 0, undefined_risk_count: 0,
  avg_kelly_optimal: null, positions: [],
} as unknown as PortfolioData;

const SHORT_CALL: OrderLeg[] = [
  {
    id: "leg-1",
    action: "SELL",
    right: "C",
    strike: 970,
    expiry: COMPACT,
    quantity: 10,
    limitPrice: 2.98,
  } as OrderLeg,
];

type SentRequest = { url: string; method: string; body: string };
const sent: SentRequest[] = [];
const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
  sent.push({ url: String(input), method: init?.method ?? "GET", body: String(init?.body ?? "") });
  return Promise.resolve(
    new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
  );
});

beforeEach(() => {
  sent.length = 0;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

type Props = {
  open?: boolean;
  legs?: OrderLeg[];
  prices?: Record<string, PriceData>;
  onClose?: () => void;
  onClearLegs?: () => void;
};

function ticket({ open = true, legs = SHORT_CALL, prices = PRICES, onClose = vi.fn(), onClearLegs = vi.fn() }: Props) {
  return (
    <MobileOrderTicket
      open={open}
      ticker="MU"
      legs={legs}
      prices={prices}
      portfolio={PORTFOLIO}
      onClose={onClose}
      onRemoveLeg={vi.fn()}
      onUpdateLeg={vi.fn()}
      onClearLegs={onClearLegs}
    />
  );
}

const submit = () => screen.queryByTestId("mobile-order-ticket-submit") as HTMLButtonElement | null;
const placeCalls = () => sent.filter((c) => c.url === "/api/orders/place");

async function reviewAndAcknowledge() {
  fireEvent.click(await screen.findByTestId("mobile-order-ticket-review"));
  fireEvent.click(await screen.findByTestId("ticket-unbounded-ack"));
  await waitFor(() => expect(submit()?.disabled).toBe(false));
}

describe("mobile ticket arms for the reviewed payload", () => {
  it("a quote tick while armed keeps the reviewed price and the acknowledgement", async () => {
    const { rerender } = render(ticket({}));
    await reviewAndAcknowledge();

    rerender(ticket({ prices: MOVED }));
    await act(async () => {});
    expect((screen.getByTestId("ticket-unbounded-ack") as HTMLInputElement).checked).toBe(true);
    expect(submit()?.disabled).toBe(false);

    await act(async () => {
      fireEvent.click(submit()!);
    });
    expect(placeCalls()).toHaveLength(1);
    expect(placeCalls()[0].method).toBe("POST");
    const body = JSON.parse(placeCalls()[0].body);
    expect(body.limitPrice).toBe(2.98);
    expect(body.quantity).toBe(10);
    expect(body.action).toBe("SELL");
  });

  it("a parent leg change while armed disarms", async () => {
    const { rerender } = render(ticket({}));
    await reviewAndAcknowledge();
    rerender(ticket({ legs: [{ ...SHORT_CALL[0], quantity: 11 }] }));
    await waitFor(() => expect(submit()).toBeNull());
    expect(screen.getByTestId("mobile-order-ticket-review")).toBeTruthy();
    expect(placeCalls()).toHaveLength(0);
  });
});

describe("mobile ticket close / reopen", () => {
  it("reopening lands on the build view, not an armed review", async () => {
    const { rerender } = render(ticket({}));
    await reviewAndAcknowledge();

    rerender(ticket({ open: false }));
    rerender(ticket({ open: true }));

    expect(submit()).toBeNull();
    expect(screen.getByTestId("mobile-order-ticket-review")).toBeTruthy();
    expect(placeCalls()).toHaveLength(0);
  });

  it("a close timer from a placed order does not fire into a reopened sheet", async () => {
    const onClose = vi.fn();
    const onClearLegs = vi.fn();
    const { rerender } = render(ticket({ onClose, onClearLegs }));
    await reviewAndAcknowledge();
    await act(async () => {
      fireEvent.click(submit()!);
    });
    await waitFor(() => expect(screen.getByTestId("mobile-order-ticket-success")).toBeTruthy());
    expect(placeCalls()).toHaveLength(1);

    // The operator closes the sheet before the auto-close fires, then opens a
    // new ticket.
    rerender(ticket({ open: false, onClose, onClearLegs }));
    rerender(ticket({ open: true, onClose, onClearLegs }));
    expect(screen.queryByTestId("mobile-order-ticket-success")).toBeNull();

    await act(async () => {
      await new Promise((r) => setTimeout(r, 1_000));
    });
    expect(onClose).not.toHaveBeenCalled();
    expect(onClearLegs).not.toHaveBeenCalled();
  });
});
