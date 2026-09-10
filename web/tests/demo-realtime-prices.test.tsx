/** @vitest-environment jsdom */

import React, { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";

import {
  RealtimePricesProvider,
  useRealtimePrices,
  type RealtimePricesValue,
  type RealtimeSubscriptionRequest,
} from "@/lib/RealtimePricesContext";
import { buildDemoRealtimeSample } from "@/lib/demo/demoRealtime";
import { optionKey } from "@/lib/pricesProtocol";

vi.mock("@/lib/RealtimeAuthContext", () => ({
  useRealtimeAuth: () => async () => "clerk-token",
}));

const CALL = { symbol: "AAPL", expiry: "20261016", strike: 280, right: "C" as const };
const REQUEST: RealtimeSubscriptionRequest = {
  symbols: ["NEM", "ES"],
  contracts: [CALL],
  indexes: [{ symbol: "VIX", exchange: "CBOE" }],
  depthSymbol: "NEM",
  depthSymbols: [],
  depthExpiry: null,
};

let latest: RealtimePricesValue | null = null;

function Probe() {
  const realtime = useRealtimePrices();
  latest = realtime;
  useEffect(() => realtime.publishSubscriptions(REQUEST), [realtime.publishSubscriptions]);
  return null;
}

beforeEach(() => {
  latest = null;
  vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
  vi.stubGlobal("fetch", vi.fn(async () => {
    throw new Error("demo realtime must not request a WebSocket ticket");
  }));
  vi.stubGlobal("WebSocket", vi.fn(() => {
    throw new Error("demo realtime must not open a WebSocket");
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("demo realtime prices provider", () => {
  it("is stable for the same symbols and sample minute", () => {
    const sampleTime = new Date("2026-09-04T18:42:15.000Z");

    expect(buildDemoRealtimeSample(REQUEST, sampleTime)).toEqual(
      buildDemoRealtimeSample(REQUEST, new Date("2026-09-04T18:42:59.000Z")),
    );
  });

  it("publishes coherent local quotes, depth, and tape without network transport", async () => {
    render(<RealtimePricesProvider><Probe /></RealtimePricesProvider>);

    await waitFor(() => expect(latest?.prices.NEM?.last).toBeTypeOf("number"));

    const value = latest!;
    const stock = value.prices.NEM;
    const option = value.prices[optionKey(CALL)];
    const index = value.prices.VIX;
    const book = value.depths.NEM;

    expect(stock.bid).toBeLessThan(stock.last!);
    expect(stock.ask).toBeGreaterThan(stock.last!);
    expect(option.undPrice).toBe(value.prices.AAPL.last);
    expect(index.last).toBeGreaterThan(0);
    expect(book).toMatchObject({ symbol: "NEM", kind: "stock", entitled: true });
    expect(book.bid[0].price).toBe(stock.bid);
    expect(book.ask[0].price).toBe(stock.ask);
    expect(value.tape.NEM.length).toBeGreaterThan(2);
    expect(value.connected).toBe(false);
    expect(value.ibConnected).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
    expect(WebSocket).not.toHaveBeenCalled();

    await expect(value.getSnapshot(["NEM", "AAPL"])).resolves.toMatchObject({
      NEM: { symbol: "NEM" },
      AAPL: { symbol: "AAPL" },
    });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("publishes an option NBBO book when the focused subject is a contract", async () => {
    function OptionProbe() {
      const realtime = useRealtimePrices();
      latest = realtime;
      useEffect(() => realtime.publishSubscriptions({
        ...REQUEST,
        contracts: [],
        depthSymbol: optionKey(CALL),
      }), [realtime.publishSubscriptions]);
      return null;
    }

    render(<RealtimePricesProvider><OptionProbe /></RealtimePricesProvider>);
    await waitFor(() => expect(latest?.depths[optionKey(CALL)]?.kind).toBe("option"));

    const book = latest!.depths[optionKey(CALL)];
    expect(book.nbbo).toMatchObject({
      bestBid: latest!.prices[optionKey(CALL)].bid,
      bestAsk: latest!.prices[optionKey(CALL)].ask,
    });
    expect(latest!.prices[optionKey(CALL)].undPrice).toBe(latest!.prices.AAPL.last);
    expect(book.bid[0].nbbo).toBe(true);
    expect(book.ask[0].nbbo).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
    expect(WebSocket).not.toHaveBeenCalled();
  });
});
