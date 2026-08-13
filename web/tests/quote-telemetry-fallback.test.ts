import { describe, it, expect } from "vitest";
import { buildQuoteTelemetryModel, type QuoteFallback } from "../lib/quoteTelemetry";
import { toFallback } from "../lib/useStockState";
import type { PriceData } from "@/lib/pricesProtocol";

const FALLBACK: QuoteFallback = {
  open: 143.55,
  high: 143.95,
  low: 141.2,
  close: 142.24,
  volume: 7896563,
  prevClose: 143.48,
};

function priceData(overrides: Partial<PriceData>): PriceData {
  return {
    symbol: "RKLB",
    last: null, lastIsCalculated: false, bid: null, ask: null, bidSize: null, askSize: null,
    volume: null, high: null, low: null, open: null, close: null,
    week52High: null, week52Low: null, avgVolume: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

describe("buildQuoteTelemetryModel — after-hours stock-state fallback", () => {
  it("returns null when there is neither live data nor a fallback (unchanged)", () => {
    expect(buildQuoteTelemetryModel(null, null)).toBeNull();
    expect(buildQuoteTelemetryModel(null)).toBeNull();
  });

  it("builds a closed-market model from the fallback when WS data is dark", () => {
    const m = buildQuoteTelemetryModel(null, FALLBACK)!;
    expect(m).not.toBeNull();
    // No live book after hours.
    expect(m.bid.value).toBe("---");
    expect(m.ask.value).toBe("---");
    expect(m.spread.value).toBe("---");
    // LAST is the prior close, explicitly labelled CLOSE so it can't pass as live.
    expect(m.last.label).toBe("CLOSE");
    expect(m.last.value).toContain("142.24");
    expect(m.high.value).toContain("143.95");
    expect(m.low.value).toContain("141.20");
    expect(m.volume.value).toBe("7,896,563");
    // DAY = (142.24 - 143.48) / 143.48 = -0.86%
    expect(m.day.value).toBe("-0.86%");
    expect(m.day.tone).toBe("negative");
  });

  it("treats a hollow tick (last/bid/ask all null, volume 0) as market-closed when a fallback exists", () => {
    // The relay emits this on weekends: an object, not null, but no live quote.
    const hollow = priceData({ last: null, bid: null, ask: null, volume: 0, high: null, low: null });
    const m = buildQuoteTelemetryModel(hollow, FALLBACK)!;
    expect(m.last.label).toBe("CLOSE");
    expect(m.last.value).toContain("142.24");
    expect(m.volume.value).toBe("7,896,563"); // volume 0 is not "live", use fallback
    expect(m.high.value).toContain("143.95");
    expect(m.day.value).toBe("-0.86%");
    expect(m.bid.value).toBe("---");
  });

  it("keeps non-null session fields from a hollow tick over the fallback", () => {
    const hollow = priceData({ last: null, bid: null, ask: null, high: 144.5, volume: 222 });
    const m = buildQuoteTelemetryModel(hollow, FALLBACK)!;
    expect(m.high.value).toContain("144.50"); // relay's session high wins
    expect(m.volume.value).toBe("222");
  });

  it("does NOT relabel LAST as CLOSE when live data is present", () => {
    const m = buildQuoteTelemetryModel(priceData({ last: 142.5, bid: 142.4, ask: 142.6 }), FALLBACK)!;
    expect(m.last.label).toBe("LAST");
    expect(m.bid.value).toContain("142.40");
  });

  it("labels a prior-session last without a fresh book as CLOSE", () => {
    const now = Date.parse("2026-08-13T15:00:00Z");
    const stale = priceData({
      last: 142.5,
      bid: null,
      ask: null,
      close: 141.5,
      timestamp: "2026-08-12T20:00:00Z",
    });
    const m = buildQuoteTelemetryModel(stale, null, now)!;
    expect(m.last.label).toBe("CLOSE");
    expect(m.last.value).toContain("142.50");
    expect(m.bid.value).toBe("---");
    expect(m.ask.value).toBe("---");
  });

  it("backfills HIGH/LOW/VOLUME from the fallback when the live stream omits them", () => {
    const m = buildQuoteTelemetryModel(priceData({ last: 142.5 }), FALLBACK)!;
    expect(m.high.value).toContain("143.95");
    expect(m.low.value).toContain("141.20");
    expect(m.volume.value).toBe("7,896,563");
  });

  it("prefers live values over the fallback when both exist", () => {
    const m = buildQuoteTelemetryModel(priceData({ last: 142.5, high: 144.0, volume: 100 }), FALLBACK)!;
    expect(m.high.value).toContain("144.00");
    expect(m.volume.value).toBe("100");
  });
});

describe("toFallback — parses UW stock_state", () => {
  it("coerces UW string fields to numbers", () => {
    const fb = toFallback({
      close: "142.24", high: "143.95", low: "141.2", open: "143.55",
      volume: 7896563, prev_close: "143.48",
    });
    expect(fb).toEqual(FALLBACK);
  });

  it("returns null for empty / non-object / all-null input", () => {
    expect(toFallback(null)).toBeNull();
    expect(toFallback({})).toBeNull();
    expect(toFallback("nope")).toBeNull();
    expect(toFallback({ close: null, high: null })).toBeNull();
  });

  it("falls back to total_volume / full_day_volume when volume is absent", () => {
    expect(toFallback({ close: "10", total_volume: 555 })?.volume).toBe(555);
    expect(toFallback({ close: "10", full_day_volume: 777 })?.volume).toBe(777);
  });
});
