// @vitest-environment jsdom

/**
 * Expirations loading fills the chain-first view and centers the readout.
 * The deck body is a row flex container, so a shrink-wrapped loader sits at
 * the start edge with its label in the upper left.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/GOOGL",
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

const css = readFileSync(join(__dirname, "../app/globals.css"), "utf8");

function ruleBody(selector: string): string {
  const at = css.indexOf(`${selector} {`);
  expect(at, `missing CSS rule ${selector}`).toBeGreaterThan(-1);
  return css.slice(at, css.indexOf("}", at));
}

function quote(): PriceData {
  return {
    symbol: "GOOGL",
    last: 331.99,
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
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("chain expirations loading", () => {
  it("centers the readout in the deck instead of parking it at the start", async () => {
    const shell = ruleBody(".chain-expiry-loading");
    expect(shell).toMatch(/display:\s*flex/);
    expect(shell).toMatch(/align-items:\s*center/);
    expect(shell).toMatch(/justify-content:\s*center/);
    expect(shell).toMatch(/flex:\s*1/);
    expect(shell).toMatch(/width:\s*100%/);
    expect(shell).toMatch(/min-height:\s*100%/);

    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(
      <TickerDetailProvider>
        <OptionsChainTab ticker="GOOGL" prices={{ GOOGL: quote() }} tickerPriceData={quote()} />
      </TickerDetailProvider>,
    );

    const loader = await screen.findByRole("status", { name: "Loading expirations" });
    const host = loader.parentElement;
    expect(host?.classList.contains("chain-expiry-loading")).toBe(true);
    expect(host?.getAttribute("style")).toBeNull();
  });
});
