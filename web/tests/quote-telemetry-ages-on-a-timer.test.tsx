/**
 * @vitest-environment jsdom
 *
 * R-253 — the single-leg staleness check must age on its own.
 *
 * `OrderQuoteTelemetry` called `buildQuoteTelemetryModel(...)` inline in the
 * render body, and that function's `nowMs` is a default parameter `= Date.now()`.
 * There is no interval, no effect, and no state that ages. A quote that was
 * fresh when last rendered stays rendered as BID/MID/ASK indefinitely, because
 * the event that would normally force a re-render is precisely the one that has
 * stopped happening — a new tick.
 *
 * `MobileChainLadder`'s strike bottom sheet is the sharpest case: c6552773
 * replaced its explicit Last and Volume rows with this panel, and the sheet
 * re-renders only on user interaction, so a sheet left open across a relay
 * stall keeps showing a live-labelled book.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { OrderQuoteTelemetry } from "@/components/QuoteTelemetry";
import type { PriceData } from "@/lib/pricesProtocol";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-08-26T15:00:00Z"));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function freshQuote(): PriceData {
  return {
    symbol: "MU", last: 133.93, lastIsCalculated: false, bid: 132.0, ask: 136.0,
    bidSize: null, askSize: null, volume: 812, high: 138.5, low: 129.75,
    open: null, close: 130.0, week52High: null, week52Low: null, avgVolume: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null,
    undPrice: null,
    timestamp: new Date().toISOString(),
  } as PriceData;
}

function labels(): string[] {
  return [...document.querySelectorAll(".price-bar-label")].map((n) => n.textContent ?? "");
}

describe("OrderQuoteTelemetry staleness", () => {
  it("renders a live book while the quote is fresh", () => {
    render(<OrderQuoteTelemetry priceData={freshQuote()} label="MU" />);
    expect(labels()).toContain("LAST");
    expect(labels()).not.toContain("CLOSE");
  });

  it("downgrades to the closed-market labels with no other re-render", async () => {
    render(<OrderQuoteTelemetry priceData={freshQuote()} label="MU" />);
    expect(labels()).toContain("LAST");

    // The relay stops ticking. Nothing else on the page changes — the props
    // are identical — so only an internal clock can move the panel off "live".
    await act(async () => {
      vi.advanceTimersByTime(6 * 60 * 1000);
    });

    expect(labels()).toContain("CLOSE");
    expect(labels()).not.toContain("LAST");
  });
});
