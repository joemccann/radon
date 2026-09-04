/**
 * REL-230: a price the UI cannot vouch for must not render as a number.
 *
 * R-607 (P1): `808f0411` guarded only the OPT branch, so a stock order with
 * `last: 0` — what IB broadcasts for a halted ticker, a pre-open contract, or
 * a relay reconnect that has not ticked — rendered a confident `$0.00`, and
 * `distanceToFill` computed `0 - limitPrice <= 0` and badged the resting
 * order as THROUGH the market.
 *
 * R-608 (P1): a combo mark built entirely from previous-session closes was
 * rendered identically to a live bid/ask mid (both prefixed `C`), and the
 * derived distance-to-fill lost even that flag.
 */
import { describe, expect, it } from "vitest";
import { resolveSingleLegLastPrice } from "../lib/orders/orderDisplay";
import { resolveRealtimePrice } from "../lib/positionUtils";
import { resolveSignedComboPrice } from "../lib/openOrderCombos";
import type { PriceData } from "../lib/pricesProtocol";

function pd(overrides: Partial<PriceData> = {}): PriceData {
  return {
    symbol: "TEST", last: null, lastIsCalculated: false,
    bid: null, ask: null, bidSize: null, askSize: null,
    volume: null, high: null, low: null, open: null, close: null,
    delta: null, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

describe("R-607: a non-option leg with last: 0 is unavailable, not $0.00", () => {
  it("refuses a zero last", () => {
    const resolved = resolveSingleLegLastPrice(
      pd({ symbol: "NVDA", last: 0, bid: null, ask: null, close: 48 }),
    );
    expect(resolved.price).toBeNull();
  });

  it("refuses a negative last", () => {
    expect(resolveSingleLegLastPrice(pd({ last: -1 })).price).toBeNull();
  });

  it("keeps a real last", () => {
    const resolved = resolveSingleLegLastPrice(pd({ last: 49 }));
    expect(resolved.price).toBe(49);
    expect(resolved.isCalculated).toBe(false);
  });

  it("passes the calculated flag through", () => {
    const resolved = resolveSingleLegLastPrice(pd({ last: 49, lastIsCalculated: true }));
    expect(resolved.isCalculated).toBe(true);
  });

  it("is undefined-safe", () => {
    expect(resolveSingleLegLastPrice(undefined).price).toBeNull();
  });
});

describe("R-608: a previous-close mark is distinguishable from a live mid", () => {
  it("flags the close fallback", () => {
    const resolved = resolveRealtimePrice(pd({ close: 12.5 }));
    expect(resolved.price).toBe(12.5);
    expect(resolved.isCalculated).toBe(true);
    expect(resolved.isPreviousClose).toBe(true);
  });

  it("does not flag a live bid/ask mid", () => {
    const resolved = resolveRealtimePrice(pd({ bid: 3.49, ask: 3.53 }));
    expect(resolved.isCalculated).toBe(true);
    expect(resolved.isPreviousClose).toBe(false);
  });

  it("does not flag a real last", () => {
    expect(resolveRealtimePrice(pd({ last: 7 })).isPreviousClose).toBe(false);
  });

  it("propagates the flag across combo legs", () => {
    const combo = resolveSignedComboPrice([
      { action: "BUY", ratio: 1, priceData: pd({ close: 5 }) },
      { action: "SELL", ratio: 1, priceData: pd({ close: 3 }) },
    ]);
    expect(combo.price).toBe(2);
    expect(combo.isPreviousClose).toBe(true);
  });

  it("a single live leg is enough to make the combo not a close mark", () => {
    const combo = resolveSignedComboPrice([
      { action: "BUY", ratio: 1, priceData: pd({ bid: 5, ask: 5.2 }) },
      { action: "SELL", ratio: 1, priceData: pd({ bid: 3, ask: 3.2 }) },
    ]);
    expect(combo.isPreviousClose).toBe(false);
  });
});

describe("R-608: the tooltip does not claim every calculated mark is a midpoint", () => {
  it("names the previous-close case", async () => {
    const { SECTION_TOOLTIPS } = await import("../lib/sectionTooltips");
    const copy = JSON.stringify(SECTION_TOOLTIPS);
    expect(copy).toMatch(/previous-session close/i);
  });
});
