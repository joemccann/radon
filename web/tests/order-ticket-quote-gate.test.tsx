/**
 * @vitest-environment jsdom
 *
 * REL-236 / R-641 (standing NF-3): no staleness gate existed on
 * order-relevant marks — a half-open relay socket keeps painting last-tick
 * prices into the ticket and nothing disarmed submit. The ticket must
 * disarm on a stale quote or a disconnected feed, asserted AT THE WIRE:
 * the armed path proves the exact request (full URL, method, payload), and
 * the closed path proves NO request fires. SingleLegOrderTicket OWNS the
 * placement fetch, so it is the component rendered here.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import SingleLegOrderTicket from "../components/SingleLegOrderTicket";
import { quoteSubmitGate, QUOTE_SUBMIT_MAX_AGE_MS } from "../lib/order/quoteSubmitGate";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// T-480: drain any queued follow-up on FAKE timers so the "nothing fired"
// window cannot lose a wall-clock race to React's commit/effect flush.
async function flushTimersFake() {
  vi.useFakeTimers();
  try {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
  } finally {
    vi.useRealTimers();
  }
}

type RecordedCall = { url: string; method: string; body: unknown };

function recordFetch(): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
      calls.push({ url, method: init?.method ?? "GET", body });
      return {
        ok: true,
        status: 200,
        json: async () => ({ status: "ok", orderId: 42 }),
      } as Response;
    }),
  );
  return calls;
}

function aaplQuote(timestamp: string): PriceData {
  return {
    symbol: "AAPL",
    last: 171,
    lastIsCalculated: false,
    bid: 170,
    ask: 172,
    bidSize: 1,
    askSize: 1,
    volume: 1000,
    high: null,
    low: null,
    open: null,
    close: 171,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp,
  };
}

const EMPTY_PORTFOLIO = { positions: [] } as unknown as PortfolioData;

function renderTicket(props: { priceData?: PriceData | null; feedConnected?: boolean }) {
  return render(
    <SingleLegOrderTicket
      defaultAction="SELL"
      defaultTif="DAY"
      quantity="100"
      onQuantityChange={() => {}}
      quantityPlaceholder="Shares"
      bid={170}
      mid={171}
      ask={172}
      isValid
      limitPrice="171.00"
      onLimitPriceChange={() => {}}
      riskInput={{
        type: "linear",
        ticker: "AAPL",
        instrument: "stock",
        action: "SELL",
        quantity: 100,
        limitPrice: 171,
        multiplier: 1,
        heldQuantity: 100,
        closeOut: { entryCostDollars: 17_000 },
        description: "SELL 100 AAPL",
      }}
      portfolio={EMPTY_PORTFOLIO}
      riskSurface="quote-gate-test"
      buildPayload={({ action, quantity, limitPrice, tif }) => ({
        type: "stock",
        symbol: "AAPL",
        action,
        quantity,
        limitPrice,
        tif,
      })}
      buildSuccessMessage={() => "Order placed: SELL 100 AAPL"}
      {...props}
    />,
  );
}

function placeButton() {
  return screen.getByRole("button", { name: /^Place Order$/ }) as HTMLButtonElement;
}

async function driveSubmit() {
  fireEvent.click(placeButton());
  const confirm = screen.queryByRole("button", { name: /Confirm Order/ });
  if (confirm) fireEvent.click(confirm);
}

describe("quoteSubmitGate (pure)", () => {
  it("is open on a fresh quote", () => {
    const now = Date.now();
    expect(
      quoteSubmitGate({ quoteTimestamp: new Date(now - 1000).toISOString(), nowMs: now }),
    ).toEqual({ open: true, reason: null });
  });

  it("closes when the quote is older than the freshness window", () => {
    const now = Date.now();
    const gate = quoteSubmitGate({
      quoteTimestamp: new Date(now - QUOTE_SUBMIT_MAX_AGE_MS - 60_000).toISOString(),
      nowMs: now,
    });
    expect(gate.open).toBe(false);
    expect(gate.reason).toMatch(/6m/);
  });

  it("closes when the feed is reported disconnected, even with a fresh quote", () => {
    const now = Date.now();
    const gate = quoteSubmitGate({
      quoteTimestamp: new Date(now).toISOString(),
      feedConnected: false,
      nowMs: now,
    });
    expect(gate.open).toBe(false);
    expect(gate.reason).toMatch(/disconnected/i);
  });

  it("stays open with no quote evidence (surfaces without a live quote keep their existing guards)", () => {
    expect(quoteSubmitGate({ quoteTimestamp: null })).toEqual({ open: true, reason: null });
    expect(quoteSubmitGate({ quoteTimestamp: "" })).toEqual({ open: true, reason: null });
  });
});

describe("SingleLegOrderTicket submit gate at the wire (R-641)", () => {
  it("ARMED: fresh quote submits the exact placement request", async () => {
    const calls = recordFetch();
    renderTicket({ priceData: aaplQuote(new Date().toISOString()) });

    await driveSubmit();

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].url).toBe("/api/orders/place");
    expect(calls[0].method).toBe("POST");
    expect(calls[0].body).toEqual({
      type: "stock",
      symbol: "AAPL",
      action: "SELL",
      quantity: 100,
      limitPrice: 171,
      tif: "DAY",
    });
  });

  it("CLOSED: a stale quote disarms submit and NO request fires", async () => {
    const calls = recordFetch();
    renderTicket({
      priceData: aaplQuote(new Date(Date.now() - QUOTE_SUBMIT_MAX_AGE_MS - 5 * 60_000).toISOString()),
    });

    expect(placeButton().disabled).toBe(true);
    await driveSubmit();
    await flushTimersFake();
    expect(calls).toHaveLength(0);
  });

  it("CLOSED: a disconnected feed disarms submit and NO request fires", async () => {
    const calls = recordFetch();
    renderTicket({
      priceData: aaplQuote(new Date().toISOString()),
      feedConnected: false,
    });

    expect(placeButton().disabled).toBe(true);
    await driveSubmit();
    await flushTimersFake();
    expect(calls).toHaveLength(0);
  });
});
