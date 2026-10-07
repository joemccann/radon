// @vitest-environment jsdom
//
// The operator resumes the iPhone PWA and taps Transmit inside the second it
// takes Clerk-js to mint a new session token. The POST reaches the middleware
// with the expired cookie and is rejected 401 BEFORE the route handler runs, so
// nothing reached IB. The ticket must end up transmitting the exact order once
// the session is refreshed, never showing a false "session expired", and must
// never resend a 401 the route itself produced.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import MobileOrderTicket from "@/components/mobile/MobileOrderTicket";
import { installSessionRefreshFetch } from "@/lib/sessionRefreshFetch";
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

/** Long call (defined risk), ten lots so a hardcoded quantity cannot pass. */
const LONG_CALL = [
  { id: "leg-1", action: "BUY" as const, right: "C" as const, strike: 970, expiry: COMPACT, quantity: 10, limitPrice: 2.98 },
];

const EXPECTED_BODY = {
  type: "option",
  symbol: "MU",
  action: "BUY",
  quantity: 10,
  tif: "DAY",
  expiry: COMPACT,
  strike: 970,
  right: "CALL",
  limitPrice: 2.98,
};

type SentRequest = { url: string; method: string; body: string };
const sent: SentRequest[] = [];
let queue: Array<() => Response> = [];

const network = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
  sent.push({ url: String(input), method: init?.method ?? "GET", body: String(init?.body ?? "") });
  const next = queue.shift();
  return Promise.resolve(next ? next() : new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
});

function middleware401(): Response {
  return new Response(JSON.stringify({ error: "Unauthorized", code: "UNAUTHORIZED" }), {
    status: 401,
    headers: {
      "Content-Type": "application/json",
      "x-clerk-auth-status": "signed-out",
      "x-clerk-auth-reason": "session-token-expired-refresh-non-eligible-non-get",
    },
  });
}

function placed(): Response {
  return new Response(JSON.stringify({ status: "ok", orderId: 41, permId: 9001, initialStatus: "Submitted" }), {
    status: 200, headers: { "Content-Type": "application/json" },
  });
}

const refresh = vi.fn(async () => "fresh-session-jwt");
let uninstall: () => void = () => {};

beforeEach(() => {
  sent.length = 0;
  queue = [];
  network.mockClear();
  refresh.mockClear();
  vi.stubGlobal("fetch", network);
  // The production wiring: SessionRefreshFetch installs over window.fetch.
  uninstall = installSessionRefreshFetch(window, refresh);
});

afterEach(() => {
  uninstall();
  cleanup();
  vi.unstubAllGlobals();
});

function renderTicket() {
  return render(
    <MobileOrderTicket
      open
      ticker="MU"
      legs={LONG_CALL}
      prices={PRICES}
      portfolio={PORTFOLIO}
      onClose={vi.fn()}
      onRemoveLeg={vi.fn()}
      onUpdateLeg={vi.fn()}
      onClearLegs={vi.fn()}
    />,
  );
}

function placeCalls(): SentRequest[] {
  return sent.filter((c) => c.url === "/api/orders/place");
}

async function armAndTransmit() {
  // Gate closed: the review step has not been taken, nothing may be on the wire.
  expect(placeCalls()).toEqual([]);
  fireEvent.click(await screen.findByTestId("mobile-order-ticket-review"));
  expect(placeCalls()).toEqual([]);
  const submit = await screen.findByTestId("mobile-order-ticket-submit");
  await waitFor(() => expect((submit as HTMLButtonElement).disabled).toBe(false));
  await act(async () => { fireEvent.click(submit); });
}

describe("mobile ticket across an expired Clerk session token", () => {
  it("refreshes the session and transmits the exact order, with no false session-expired error", async () => {
    queue = [middleware401, placed];
    renderTicket();
    await armAndTransmit();

    await waitFor(() => expect(placeCalls()).toHaveLength(2));
    expect(refresh).toHaveBeenCalledTimes(1);
    for (const call of placeCalls()) {
      expect(call.url).toBe("/api/orders/place");
      expect(call.method).toBe("POST");
      expect(JSON.parse(call.body)).toEqual(EXPECTED_BODY);
    }
    expect(await screen.findByTestId("mobile-order-ticket-success")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not resend when the 401 came from the route handler, not the middleware", async () => {
    queue = [() => new Response(JSON.stringify({ error: "Unauthorized" }), {
      status: 401, headers: { "Content-Type": "application/json", "x-clerk-auth-status": "signed-in" },
    })];
    renderTicket();
    await armAndTransmit();

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeNull());
    expect(placeCalls()).toHaveLength(1);
    expect(JSON.parse(placeCalls()[0].body)).toEqual(EXPECTED_BODY);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("does not resend when there is no live session to refresh", async () => {
    refresh.mockResolvedValueOnce(null as unknown as string);
    queue = [middleware401];
    renderTicket();
    await armAndTransmit();

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeNull());
    expect(placeCalls()).toHaveLength(1);
  });
});
