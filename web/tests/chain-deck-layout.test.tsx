// @vitest-environment jsdom

/**
 * Chain deck layout contract.
 *
 * The chain deck is a height-locked workspace: the deck body itself must NOT
 * scroll, the chain table and the docked order ticket each scroll internally.
 * Before this contract the ticket was sized with `calc(100dvh - 96px)` while
 * its scrollport was the (much shorter) deck body, so the ticket overflowed
 * the deck without ever growing its own scrollbar — the bottom of the ticket
 * (risk block + transmit) was only reachable by scrolling the container
 * AROUND the form.
 *
 * The toolbar also belongs to the chain column, not above the rail: parked
 * above it, it left a dead band of empty panel beside it where the ticket
 * should start.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/test",
  useRouter: () => ({
    replace: vi.fn(),
    push: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
}));

import OptionsChainTab from "../components/ticker-detail/OptionsChainTab";
import { TickerDetailProvider } from "../lib/TickerDetailContext";
import type { PriceData } from "../lib/pricesProtocol";

const TICKER = "PLTR";
const EXPIRY = "20991231";
const STRIKES = [148, 150, 152.5, 155, 157.5];
const SPOT = 153.1;

function pd(over: Partial<PriceData>): PriceData {
  return {
    symbol: "X",
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
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
    ...over,
  };
}

function installFetchMock() {
  const fetchMock = vi.fn<typeof fetch>();
  fetchMock.mockImplementation((input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : String((input as Request).url);
    const json = (body: unknown) =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    if (url.includes("/api/options/expirations")) return json({ symbol: TICKER, expirations: [EXPIRY] });
    if (url.includes("/api/options/chain")) return json({ symbol: TICKER, expiry: EXPIRY, strikes: STRIKES });
    if (url.includes("/api/risk-free-rate")) return json({ rate: 0 });
    if (url.includes("/api/previous-close")) return json({ closes: { [TICKER]: SPOT } });
    return Promise.reject(new Error(`Unexpected fetch: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const css = readFileSync(join(__dirname, "../app/globals.css"), "utf8");

function ruleBody(selector: string): string {
  const at = css.indexOf(`${selector} {`);
  expect(at, `missing CSS rule ${selector}`).toBeGreaterThan(-1);
  return css.slice(at, css.indexOf("}", at));
}

describe("chain deck — the ticket scrolls itself, not the container around it", () => {
  it("hands the deck body's height to the chain instead of scrolling it", () => {
    const deckBody = ruleBody(".asset-deck-body:has(.chain-tab)");
    expect(deckBody).toMatch(/overflow:\s*hidden/);

    const tab = ruleBody(".chain-tab");
    expect(tab).toMatch(/display:\s*flex/);
    expect(tab).toMatch(/min-height:\s*0/);
  });

  it("sizes the docked ticket by its cell, never by viewport math", () => {
    const rail = ruleBody(".order-builder--rail");
    expect(rail).toMatch(/overflow-y:\s*auto/);
    expect(rail).toMatch(/max-height:\s*100%/);
    expect(rail).toMatch(/align-self:\s*start/);
    expect(rail).not.toMatch(/dvh|vh\b/);
    expect(rail).not.toMatch(/position:\s*sticky/);
  });

  it("lets the chain table fill the rail rather than a fixed 520px box", () => {
    const wrapper = ruleBody(".chain-rail .chain-grid-wrapper");
    expect(wrapper).toMatch(/flex:\s*0 1 auto/);
    expect(wrapper).toMatch(/min-height:\s*0/);
    expect(wrapper).toMatch(/max-height:\s*none/);

    const railBody = ruleBody(".chain-rail");
    expect(railBody).toMatch(/flex:\s*1/);
    expect(railBody).toMatch(/min-height:\s*0/);
    expect(railBody).toMatch(/align-items:\s*stretch/);
  });
});

describe("chain deck — the toolbar rides with the chain column", () => {
  it("nests the expiry bar inside the chain column so the ticket starts at the top", async () => {
    installFetchMock();
    const { container } = render(
      React.createElement(
        TickerDetailProvider,
        null,
        React.createElement(OptionsChainTab, {
          ticker: TICKER,
          prices: { [TICKER]: pd({ last: SPOT }) },
          tickerPriceData: pd({ last: SPOT }),
        }),
      ),
    );

    await waitFor(() => {
      expect(container.querySelector(".chain-expiry-bar")).not.toBeNull();
    });

    expect(container.querySelector(".chain-rail-main > .chain-expiry-bar")).not.toBeNull();
    expect(container.querySelector(".chain-tab > .chain-expiry-bar")).toBeNull();
  });
});
