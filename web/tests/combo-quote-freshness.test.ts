/**
 * @vitest-environment node
 *
 * R-208 — the combo freshness gate is structurally unreachable.
 *
 * `comboQuotePriceData` stamped `timestamp: new Date().toISOString()` on every
 * call, and `hasLiveQuote` rejects a PriceData only when its timestamp is older
 * than LIVE_QUOTE_MAX_AGE_MS. A synthesized combo quote is therefore always
 * "now", so the CLOSE/HIGH/LOW downgrade branch can never fire for a combo no
 * matter how old the leg quotes that produced netBid/netAsk are — on every
 * surface a trader prices a combo from.
 *
 * `lastIsCalculated: true` was likewise unconditional, mislabelling a genuine
 * traded `last` as derived. The `last` field exists precisely so a caller can
 * pass a real one; it is honest today only by accident of all four call sites
 * passing `mid`.
 */
import { describe, expect, it } from "vitest";

import { buildQuoteTelemetryModel, comboQuotePriceData, oldestQuoteTimestamp } from "../lib/quoteTelemetry";

const NOW = Date.parse("2026-08-26T15:00:00Z");
const TEN_MINUTES_AGO = new Date(NOW - 10 * 60 * 1000).toISOString();
const ONE_MINUTE_AGO = new Date(NOW - 60 * 1000).toISOString();

describe("comboQuotePriceData freshness", () => {
  it("carries the leg timestamp it was built from", () => {
    const quote = comboQuotePriceData({
      symbol: "MU", bid: 1.0, ask: 1.2, last: 1.1, timestamp: TEN_MINUTES_AGO,
    });
    expect(quote.timestamp).toBe(TEN_MINUTES_AGO);
  });

  it("lets the staleness gate reject a stale combo book", () => {
    const quote = comboQuotePriceData({
      symbol: "MU", bid: 1.0, ask: 1.2, last: 1.1, timestamp: TEN_MINUTES_AGO,
    });
    const model = buildQuoteTelemetryModel(quote, null, NOW)!;
    // The CLOSE/HIGH/LOW downgrade: bid/mid/ask blank out and `last` is
    // relabelled CLOSE. This branch was previously unreachable for a combo.
    expect(model.last.label).toBe("CLOSE");
    expect(model.bid.value).toBe("---");
    expect(model.ask.value).toBe("---");
  });

  it("still renders a fresh combo book", () => {
    const quote = comboQuotePriceData({
      symbol: "MU", bid: 1.0, ask: 1.2, last: 1.1, timestamp: ONE_MINUTE_AGO,
    });
    const model = buildQuoteTelemetryModel(quote, null, NOW)!;
    expect(model.last.label).toBe("MARK");
    expect(model.bid.value).not.toBe("---");
  });

  it("lets a caller declare a genuine traded net last", () => {
    // The wrapper used to assert `lastIsCalculated: true` unconditionally,
    // which is honest only by accident of every current caller passing the
    // net mid. It is now the caller's statement, not the wrapper's guess.
    const quote = comboQuotePriceData({
      symbol: "MU", bid: 1.0, ask: 1.2, last: 1.15,
      lastIsCalculated: false, timestamp: ONE_MINUTE_AGO,
    });
    expect(quote.lastIsCalculated).toBe(false);
  });

  it("still defaults a derived last to calculated", () => {
    const quote = comboQuotePriceData({
      symbol: "MU", bid: 1.0, ask: 1.2, timestamp: ONE_MINUTE_AGO,
    });
    expect(quote.last).toBeCloseTo(1.1);
    expect(quote.lastIsCalculated).toBe(true);
  });

  it("treats a missing timestamp as not-live rather than as now", () => {
    // A caller that cannot establish the age of its legs must not be handed a
    // quote that claims to be current.
    const quote = comboQuotePriceData({ symbol: "MU", bid: 1.0, ask: 1.2 });
    expect(buildQuoteTelemetryModel(quote, null, NOW)!.last.label).toBe("CLOSE");
  });
});

describe("oldestQuoteTimestamp", () => {
  it("picks the oldest leg, because a combo is only as fresh as its stalest leg", () => {
    expect(oldestQuoteTimestamp([
      { timestamp: ONE_MINUTE_AGO } as never,
      { timestamp: TEN_MINUTES_AGO } as never,
    ])).toBe(TEN_MINUTES_AGO);
  });

  it("returns null when any leg has no usable stamp", () => {
    // One unknown leg makes the whole net's age unknown; guessing "now" is the
    // defect this exists to prevent.
    expect(oldestQuoteTimestamp([
      { timestamp: ONE_MINUTE_AGO } as never,
      { timestamp: "" } as never,
    ])).toBeNull();
    expect(oldestQuoteTimestamp([{ timestamp: ONE_MINUTE_AGO } as never, null])).toBeNull();
  });

  it("returns null for an empty set", () => {
    expect(oldestQuoteTimestamp([])).toBeNull();
  });
});
