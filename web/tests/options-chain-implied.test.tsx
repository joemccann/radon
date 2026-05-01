// @vitest-environment jsdom

/**
 * Component test: OptionsChainTab renders an "Implied" (Black-Scholes)
 * column on both the call and put sides, derived from the same resolver
 * the dashboard PositionTable uses (lib/impliedValue.ts → lib/blackScholes.ts).
 *
 * Inputs:
 *   S      ← prices[TICKER].last
 *   σ      ← prices[optionKey].impliedVol (stream) → bisection on close (fallback)
 *   K      ← row strike
 *   T      ← (expiry@16:00 ET − now) / 365
 *   r      ← useRiskFreeRate() (FRED DFF, mocked to 0 here via unmocked fetch)
 *   right  ← "Call" on the call side, "Put" on the put side
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";

import OptionsChainTab from "../components/ticker-detail/OptionsChainTab";
import { TickerDetailProvider } from "../lib/TickerDetailContext";
import { bsCall, bsPut } from "../lib/blackScholes";
import { yearsToExpiry } from "../lib/impliedValue";
import { optionKey } from "../lib/pricesProtocol";
import type { PriceData } from "../lib/pricesProtocol";

const TICKER = "PLTR";
const EXPIRY = "20991231"; // far future so T > 0 regardless of when the test runs
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

function buildPrices(extras: Record<string, PriceData> = {}): Record<string, PriceData> {
  return { [TICKER]: pd({ last: SPOT }), ...extras };
}

function callKeyFor(strike: number): string {
  return optionKey({ symbol: TICKER, expiry: EXPIRY, strike, right: "C" });
}
function putKeyFor(strike: number): string {
  return optionKey({ symbol: TICKER, expiry: EXPIRY, strike, right: "P" });
}

function renderChain(prices: Record<string, PriceData>) {
  return render(
    React.createElement(
      TickerDetailProvider,
      null,
      React.createElement(OptionsChainTab, {
        ticker: TICKER,
        prices,
        tickerPriceData: prices[TICKER] ?? null,
      }),
    ),
  );
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
    if (url.includes("/api/options/expirations")) {
      return Promise.resolve(
        new Response(JSON.stringify({ symbol: TICKER, expirations: [EXPIRY] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
    if (url.includes("/api/options/chain")) {
      return Promise.resolve(
        new Response(JSON.stringify({ symbol: TICKER, expiry: EXPIRY, strikes: STRIKES }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
    // useRiskFreeRate hits /api/risk-free-rate; let it resolve with 0
    if (url.includes("/api/risk-free-rate")) {
      return Promise.resolve(
        new Response(JSON.stringify({ rate: 0 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
    if (url.includes("/api/previous-close")) {
      return Promise.resolve(
        new Response(JSON.stringify({ closes: { [TICKER]: SPOT } }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
    return Promise.reject(new Error(`Unexpected fetch: ${url}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("OptionsChainTab — Implied (Black-Scholes) column", () => {
  beforeEach(() => {
    installFetchMock();
    // No-op the chain wrapper auto-scroll so jsdom doesn't error.
    Object.defineProperty(Element.prototype, "scrollTo", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders an 'Implied' header on the call side and on the put side", async () => {
    renderChain(buildPrices());

    await waitFor(() => {
      expect(document.querySelector(".chain-grid-wrapper")).not.toBeNull();
    });

    const headers = Array.from(document.querySelectorAll(".chain-header")).map(
      (h) => h.textContent ?? "",
    );
    const impliedCount = headers.filter((t) => t.trim() === "Implied").length;
    // One on the call side, one on the put side.
    expect(impliedCount).toBe(2);
  });

  it("renders BS-derived call and put implied prices for streamed IV", async () => {
    const sigma = 0.45;
    const strike = 152.5;
    const prices = buildPrices({
      [callKeyFor(strike)]: pd({ impliedVol: sigma }),
      [putKeyFor(strike)]: pd({ impliedVol: sigma }),
    });

    renderChain(prices);

    await waitFor(() => {
      expect(document.querySelector(".chain-grid-wrapper")).not.toBeNull();
    });

    // Math parity: same inputs the resolver uses.
    const T = yearsToExpiry(EXPIRY, new Date())!;
    const expectedCall = bsCall(SPOT, strike, T, 0, sigma);
    const expectedPut = bsPut(SPOT, strike, T, 0, sigma);

    await waitFor(() => {
      const impliedCells = document.querySelectorAll(".chain-implied");
      expect(impliedCells.length).toBeGreaterThan(0);
      const texts = Array.from(impliedCells).map((c) => c.textContent ?? "");
      // Cell rendering uses fmtPrice (USD, 2 decimals) — match the existing
      // chain numeric style. Math parity is enforced by black-scholes.test.ts.
      expect(texts.some((t) => t.includes(`$${expectedCall.toFixed(2)}`))).toBe(true);
      expect(texts.some((t) => t.includes(`$${expectedPut.toFixed(2)}`))).toBe(true);
    });
  });

  it("falls back to bisection on close when streaming IV is null", async () => {
    const sigma = 0.32;
    const strike = 155;
    const T = yearsToExpiry(EXPIRY, new Date())!;
    // Yesterday's T ≈ today's T + 1/365 (matches resolveSigma in impliedValue.ts).
    const T_y = T + 1 / 365;
    const yesterdayCall = bsCall(SPOT, strike, T_y, 0, sigma);
    const yesterdayPut = bsPut(SPOT, strike, T_y, 0, sigma);

    const prices = buildPrices({
      [TICKER]: pd({ last: SPOT, close: SPOT }),
      [callKeyFor(strike)]: pd({ impliedVol: null, close: yesterdayCall }),
      [putKeyFor(strike)]: pd({ impliedVol: null, close: yesterdayPut }),
    });

    renderChain(prices);

    await waitFor(() => {
      expect(document.querySelector(".chain-grid-wrapper")).not.toBeNull();
    });

    // Today's BS price using back-solved σ ≈ today's BS at original σ
    // (T differs only by one calendar day → bias is small).
    const expectedCallToday = bsCall(SPOT, strike, T, 0, sigma);
    const expectedPutToday = bsPut(SPOT, strike, T, 0, sigma);

    await waitFor(() => {
      const cells = Array.from(document.querySelectorAll(".chain-implied")).map(
        (c) => c.textContent ?? "",
      );
      // At least one cell should hold a finite, near-expected value (within $0.05).
      const numericValues = cells
        .map((t) => parseFloat(t.replace(/[^0-9.\-]/g, "")))
        .filter((n) => Number.isFinite(n));
      expect(numericValues.some((v) => Math.abs(v - expectedCallToday) < 0.05)).toBe(true);
      expect(numericValues.some((v) => Math.abs(v - expectedPutToday) < 0.05)).toBe(true);
    });
  });

  it("renders empty Implied cell when neither stream nor close is available", async () => {
    const strike = 150;
    const prices = buildPrices({
      [callKeyFor(strike)]: pd({ impliedVol: null, close: null }),
      [putKeyFor(strike)]: pd({ impliedVol: null, close: null }),
    });

    renderChain(prices);

    await waitFor(() => {
      expect(document.querySelector(".chain-grid-wrapper")).not.toBeNull();
    });

    // Find the row for strike 150 and confirm both Implied cells are blank.
    await waitFor(() => {
      const rows = Array.from(document.querySelectorAll(".chain-row"));
      const targetRow = rows.find((r) => {
        const strikeCell = r.querySelector(".chain-strike");
        return strikeCell?.textContent?.includes("150");
      });
      expect(targetRow).toBeDefined();
      const impliedCellsInRow = targetRow!.querySelectorAll(".chain-implied");
      expect(impliedCellsInRow.length).toBeGreaterThan(0);
      for (const cell of Array.from(impliedCellsInRow)) {
        expect(cell.textContent).toBe("");
      }
    });
  });
});
