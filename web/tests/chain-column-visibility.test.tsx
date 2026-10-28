// @vitest-environment jsdom

/**
 * Options chain column visibility: every greek and quote heading can be
 * toggled from the toolbar COLUMNS menu. One visibility map drives both
 * sides (calls outer → strike, puts mirrored), persisted under
 * `radon:columns:options-chain`.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/test",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
}));

import OptionsChainTab from "../components/ticker-detail/OptionsChainTab";
import { TickerDetailProvider } from "../lib/TickerDetailContext";
import { bsRho } from "../lib/blackScholes";
import { yearsToExpiry } from "../lib/impliedValue";
import { optionKey } from "../lib/pricesProtocol";
import type { PriceData } from "../lib/pricesProtocol";
import { __resetUiPreferencesForTests } from "../lib/uiPreferences";

const TICKER = "IWO";
const EXPIRY = "20991231";
const STRIKE = 355;
const SPOT = 352.85;

function pd(over: Partial<PriceData>): PriceData {
  return {
    symbol: "X", last: null, lastIsCalculated: false, bid: null, ask: null,
    bidSize: null, askSize: null, volume: null, high: null, low: null, open: null,
    close: null, week52High: null, week52Low: null, avgVolume: null, delta: null,
    gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(), ...over,
  };
}

const callKey = optionKey({ symbol: TICKER, expiry: EXPIRY, strike: STRIKE, right: "C" });
const putKey = optionKey({ symbol: TICKER, expiry: EXPIRY, strike: STRIKE, right: "P" });

const PRICES: Record<string, PriceData> = {
  [TICKER]: pd({ last: SPOT }),
  [callKey]: pd({
    bid: 9.4, ask: 12.2, last: 10.8, volume: 4, delta: 0.512, gamma: 0.01234,
    theta: -0.0712, vega: 0.5321, impliedVol: 0.199, bidIv: 0.185, askIv: 0.214,
    optPrice: 10.77,
  }),
  [putKey]: pd({
    bid: 10.6, ask: 13.9, last: 12.25, delta: -0.488, gamma: 0.01234,
    theta: -0.0601, vega: 0.5321, impliedVol: 0.199, bidIv: 0.19, askIv: 0.22,
    optPrice: 12.4,
  }),
};

function installFetchMock() {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>((input) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : (input as Request).url;
    const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
    if (url.includes("/api/options/expirations")) return json({ symbol: TICKER, expirations: [EXPIRY] });
    if (url.includes("/api/options/chain")) return json({ symbol: TICKER, expiry: EXPIRY, strikes: [350, STRIKE, 360] });
    if (url.includes("/api/risk-free-rate")) return json({ rate: 0 });
    if (url.includes("/api/previous-close")) return json({ closes: { [TICKER]: SPOT } });
    if (url.includes("/api/profile")) return json({});
    return Promise.reject(new Error(`Unexpected fetch: ${url}`));
  }));
}

function renderChain() {
  return render(
    <TickerDetailProvider>
      <OptionsChainTab ticker={TICKER} prices={PRICES} tickerPriceData={PRICES[TICKER]} />
    </TickerDetailProvider>,
  );
}

function headerTexts(): string[] {
  const head = screen.getByTestId("chain-columns-header");
  return Array.from(head.querySelectorAll("tr:first-child th")).map((th) => (th.textContent ?? "").trim());
}

async function rowFor(strike: number): Promise<HTMLTableRowElement> {
  await waitFor(() => expect(document.querySelector(`tr[data-strike="${strike}"]`)).not.toBeNull());
  return document.querySelector(`tr[data-strike="${strike}"]`) as HTMLTableRowElement;
}

function openMenu() {
  fireEvent.click(screen.getByRole("button", { name: /columns/i }));
  return screen.getByRole("menu");
}

beforeEach(() => {
  window.localStorage.clear();
  __resetUiPreferencesForTests();
  installFetchMock();
  Object.defineProperty(Element.prototype, "scrollTo", { configurable: true, value: vi.fn() });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("options chain column visibility", () => {
  it("defaults to the shipped layout, mirrored around the strike", async () => {
    renderChain();
    await rowFor(STRIKE);
    expect(headerTexts()).toEqual([
      "Δ", "IV", "Implied", "Vol", "Bid", "Mid", "Ask", "Last",
      "Strike",
      // Puts mirror the call side except Bid → Mid → Ask keeps reading order.
      "Last", "Bid", "Mid", "Ask", "Vol", "Implied", "IV", "Δ",
    ]);
    // Cells line up with headers on every row.
    expect((await rowFor(STRIKE)).querySelectorAll("td")).toHaveLength(17);
  });

  it("lists every greek and heading in the menu, grouped", async () => {
    renderChain();
    await rowFor(STRIKE);
    const menu = openMenu();
    for (const group of ["Greeks", "Volatility", "Valuation", "Quote"]) {
      expect(within(menu).getByRole("group", { name: group })).toBeTruthy();
    }
    for (const label of ["Delta", "Gamma", "Theta", "Vega", "Rho", "IV", "Bid IV", "Ask IV", "Model price", "Implied", "Volume", "Bid", "Mid", "Ask", "Last"]) {
      expect(within(menu).getByRole("checkbox", { name: label })).toBeTruthy();
    }
  });

  it("toggling Gamma on adds a Γ column to both sides with IB's value", async () => {
    renderChain();
    const row = await rowFor(STRIKE);
    fireEvent.click(within(openMenu()).getByRole("checkbox", { name: "Gamma" }));
    const headers = headerTexts();
    expect(headers.filter((h) => h === "Γ")).toHaveLength(2);
    expect(headers.indexOf("Γ")).toBe(1);
    expect(headers.lastIndexOf("Γ")).toBe(headers.length - 2);
    const cells = Array.from(row.querySelectorAll("td")).map((td) => td.textContent);
    expect(cells).toHaveLength(19);
    expect(cells[1]).toBe("0.0123");
    expect(cells[cells.length - 2]).toBe("0.0123");
  });

  it("renders theta, vega, bid/ask IV and model price from the IB stream", async () => {
    renderChain();
    const row = await rowFor(STRIKE);
    const menu = openMenu();
    for (const name of ["Theta", "Vega", "Bid IV", "Ask IV", "Model price"]) {
      fireEvent.click(within(menu).getByRole("checkbox", { name }));
    }
    const cell = (key: string) => row.querySelector(`[data-col="call-${key}"]`)?.textContent;
    expect(cell("theta")).toBe("-0.071");
    expect(cell("vega")).toBe("0.532");
    expect(cell("bidIv")).toBe("18.5");
    expect(cell("askIv")).toBe("21.4");
    expect(cell("model")).toBe("$10.77");
    expect(row.querySelector('[data-col="put-model"]')?.textContent).toBe("$12.40");
  });

  it("rho is Black-Scholes derived from the same inputs as Implied", async () => {
    renderChain();
    const row = await rowFor(STRIKE);
    fireEvent.click(within(openMenu()).getByRole("checkbox", { name: "Rho" }));
    const T = yearsToExpiry(EXPIRY, new Date())!;
    const expected = bsRho({ S: SPOT, K: STRIKE, T, r: 0, sigma: 0.199, type: "Call" });
    expect(row.querySelector('[data-col="call-rho"]')?.textContent).toBe(expected.toFixed(3));
  });

  it("hiding Bid removes the bid header and cells on both sides", async () => {
    renderChain();
    const row = await rowFor(STRIKE);
    fireEvent.click(within(openMenu()).getByRole("checkbox", { name: "Bid" }));
    expect(headerTexts()).not.toContain("Bid");
    expect(row.querySelector('[data-col="call-bid"]')).toBeNull();
    expect(row.querySelector('[data-col="put-bid"]')).toBeNull();
    expect(row.querySelectorAll("td")).toHaveLength(15);
  });

  it("group None / All hides and shows every greek at once", async () => {
    renderChain();
    await rowFor(STRIKE);
    const menu = openMenu();
    const greeks = within(menu).getByRole("group", { name: "Greeks" });
    fireEvent.click(within(greeks).getByRole("button", { name: "Hide all Greeks" }));
    expect(headerTexts()).not.toContain("Δ");
    fireEvent.click(within(greeks).getByRole("button", { name: "Show all Greeks" }));
    const headers = headerTexts();
    for (const h of ["Δ", "Γ", "Θ", "Vega", "ρ"]) expect(headers.filter((x) => x === h)).toHaveLength(2);
  });

  it("side-label colSpan tracks the visible column count", async () => {
    renderChain();
    await rowFor(STRIKE);
    fireEvent.click(within(openMenu()).getByRole("checkbox", { name: "Vega" }));
    const labels = screen.getByTestId("chain-columns-header").querySelectorAll("tr:nth-child(2) th[colspan]");
    expect(Array.from(labels).map((th) => th.getAttribute("colspan"))).toEqual(["9", "9"]);
  });

  it("persists the choice and restores it on remount", async () => {
    const { unmount } = renderChain();
    await rowFor(STRIKE);
    fireEvent.click(within(openMenu()).getByRole("checkbox", { name: "Theta" }));
    await waitFor(() => {
      const saved = JSON.parse(window.localStorage.getItem("radon:columns:options-chain") ?? "{}");
      expect(saved.theta).toBe(true);
    });
    unmount();
    renderChain();
    await rowFor(STRIKE);
    await waitFor(() => expect(headerTexts().filter((h) => h === "Θ")).toHaveLength(2));
  });

  it("keeps trading clicks on the cells that remain", async () => {
    renderChain();
    const row = await rowFor(STRIKE);
    fireEvent.click(within(openMenu()).getByRole("checkbox", { name: "Gamma" }));
    fireEvent.click(row.querySelector('[data-col="call-ask"]') as HTMLElement);
    await waitFor(() => expect(document.querySelectorAll('[data-testid="order-builder-leg"]').length).toBe(1));
  });
});
