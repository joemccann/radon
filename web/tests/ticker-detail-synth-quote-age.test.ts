/**
 * @vitest-environment node
 *
 * R-271 — a synthesized leg quote must not fabricate its own age.
 *
 * When the realtime feed has no PriceData for a leg, TickerDetailContent
 * synthesizes one whose `last` is `leg.market_price` — the mark captured at the
 * last portfolio sync, potentially hours old — and stamped it
 * `timestamp: new Date().toISOString()`, i.e. the render clock. The object is
 * then handed to consumers as an ordinary PriceData, and BookTab reads
 * `.timestamp` off exactly this type. `lastIsCalculated: true` marks the PRICE
 * as derived, but the AGE was fabricated: any consumer that ages this quote
 * concludes it is milliseconds old. The correct value — the portfolio
 * snapshot's `last_sync` — is available in the same tree.
 */
import { describe, expect, it } from "vitest";

import { resolveTickerQuoteTelemetry } from "../components/TickerDetailContent";
import type { PortfolioPosition } from "../lib/types";

const LAST_SYNC = "2026-08-26T13:05:00.000Z";

function illiquidSingleLeg(): PortfolioPosition {
  return {
    id: 11,
    ticker: "MU",
    structure: "Long Call",
    structure_type: "Long Call",
    direction: "LONG",
    contracts: 1,
    expiry: "2026-07-17",
    legs: [
      {
        direction: "LONG", type: "Call", strike: 1050, contracts: 1,
        avg_cost: 100, entry_cost: 100,
        market_price: 133.93, market_price_is_calculated: true,
      },
    ],
  } as unknown as PortfolioPosition;
}

describe("resolveTickerQuoteTelemetry synthesized quote", () => {
  it("stamps the portfolio sync time, not the render clock", () => {
    const { priceData } = resolveTickerQuoteTelemetry("MU", illiquidSingleLeg(), {}, LAST_SYNC);
    expect(priceData).not.toBeNull();
    expect(priceData!.timestamp).toBe(LAST_SYNC);
  });

  it("still marks the price itself as calculated", () => {
    const { priceData } = resolveTickerQuoteTelemetry("MU", illiquidSingleLeg(), {}, LAST_SYNC);
    expect(priceData!.lastIsCalculated).toBe(true);
    expect(priceData!.last).toBe(133.93);
  });

  it("does not claim an age it cannot know", () => {
    // No sync timestamp available: the quote must read as not-live rather than
    // inherit `now`.
    const { priceData } = resolveTickerQuoteTelemetry("MU", illiquidSingleLeg(), {}, null);
    expect(priceData!.timestamp).toBe("");
  });
});
