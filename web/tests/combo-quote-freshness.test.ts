import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { optionKey, type PriceData } from "@/lib/pricesProtocol";
import {
  buildQuoteTelemetryModel,
  comboQuotePriceData,
  oldestQuoteTimestamp,
} from "@/lib/quoteTelemetry";
import { computeNetOptionQuote, type OrderLeg } from "@/lib/optionsChainUtils";
import { resolveNaturalSpreadQuote } from "@/lib/positionUtils";
import { resolveOrderPriceData } from "@/components/ModifyOrderModal";
import type { OpenOrder, PortfolioPosition } from "@/lib/types";

const NOW = Date.parse("2026-08-13T15:00:00Z");
const STALE = "2026-08-12T20:00:00Z";
const FRESH = "2026-08-13T14:58:00Z";

function legQuote(over: Partial<PriceData> & { symbol: string }): PriceData {
  return {
    last: null, lastIsCalculated: false, bid: null, ask: null, bidSize: null, askSize: null,
    volume: null, high: null, low: null, open: null, close: null, week52High: null,
    week52Low: null, avgVolume: null, delta: null, gamma: null, theta: null, vega: null,
    impliedVol: null, undPrice: null, timestamp: FRESH,
    ...over,
  };
}

describe("T-158 — a combo net quote is only as fresh as its stalest leg", () => {
  // Pin the wall clock to the same instant the assertions call "now", so the
  // bug under test (stamping `new Date()`) cannot pass by accident via the
  // future-timestamp guard in `hasLiveQuote`.
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("relabels a stale combo net quote to CLOSE instead of rendering it as live", () => {
    const model = buildQuoteTelemetryModel(
      comboQuotePriceData({ symbol: "AAOI", bid: -1.2, ask: -0.8, last: -1.0, timestamp: STALE }),
      null,
      NOW,
    )!;
    expect(model.last.label).toBe("CLOSE");
    expect(model.bid.value).toBe("---");
    expect(model.ask.value).toBe("---");
    expect(model.mid.value).toBe("---");
    expect(model.spread.value).toBe("---");
  });

  it("keeps a combo live when every leg ticked inside the 5 minute window", () => {
    const model = buildQuoteTelemetryModel(
      comboQuotePriceData({ symbol: "AAOI", bid: -1.2, ask: -0.8, last: -1.0, timestamp: FRESH }),
      null,
      NOW,
    )!;
    expect(model.last.label).toBe("MARK");
    expect(model.bid.value).toBe("$-1.20");
  });

  it("fails closed when the caller has no leg freshness at all", () => {
    const model = buildQuoteTelemetryModel(
      comboQuotePriceData({ symbol: "AAOI", bid: -1.2, ask: -0.8, last: -1.0 }),
      null,
      NOW,
    )!;
    expect(model.last.label).toBe("CLOSE");
    expect(model.bid.value).toBe("---");
  });

  describe("oldestQuoteTimestamp", () => {
    it("returns the stalest leg timestamp", () => {
      expect(
        oldestQuoteTimestamp([
          legQuote({ symbol: "A", timestamp: FRESH }),
          legQuote({ symbol: "B", timestamp: STALE }),
        ]),
      ).toBe(STALE);
    });

    it("returns undefined when any leg has no parseable timestamp", () => {
      expect(oldestQuoteTimestamp([legQuote({ symbol: "A" }), null])).toBeUndefined();
      expect(
        oldestQuoteTimestamp([legQuote({ symbol: "A", timestamp: "" })]),
      ).toBeUndefined();
      expect(oldestQuoteTimestamp([])).toBeUndefined();
    });
  });

  it("computeNetOptionQuote reports the oldest leg timestamp as asOf", () => {
    const legs: OrderLeg[] = [
      { id: "1", action: "BUY", right: "C", strike: 200, expiry: "20260918", quantity: 1, limitPrice: null },
      { id: "2", action: "SELL", right: "C", strike: 210, expiry: "20260918", quantity: 1, limitPrice: null },
    ];
    const prices: Record<string, PriceData> = {
      [optionKey({ symbol: "AAPL", expiry: "20260918", strike: 200, right: "C" })]: legQuote({ symbol: "AAPL", bid: 5, ask: 5.4, timestamp: FRESH }),
      [optionKey({ symbol: "AAPL", expiry: "20260918", strike: 210, right: "C" })]: legQuote({ symbol: "AAPL", bid: 2, ask: 2.4, timestamp: STALE }),
    };
    const net = computeNetOptionQuote(legs, prices, "AAPL");
    expect(net.asOf).toBe(STALE);
    const model = buildQuoteTelemetryModel(
      comboQuotePriceData({ symbol: "AAPL", bid: net.bid, ask: net.ask, last: net.mid, timestamp: net.asOf }),
      null,
      NOW,
    )!;
    expect(model.last.label).toBe("CLOSE");
  });

  it("resolveNaturalSpreadQuote reports the oldest leg timestamp as asOf", () => {
    const position = {
      ticker: "AMD",
      expiry: "2026-09-18",
      contracts: 1,
      structure_type: "Bull Call Spread",
      legs: [
        { type: "Call", strike: 200, direction: "LONG", contracts: 1 },
        { type: "Call", strike: 210, direction: "SHORT", contracts: 1 },
      ],
    } as unknown as PortfolioPosition;
    const prices: Record<string, PriceData> = {
      [optionKey({ symbol: "AMD", expiry: "20260918", strike: 200, right: "C" })]: legQuote({ symbol: "AMD", bid: 5, ask: 5.4, timestamp: STALE }),
      [optionKey({ symbol: "AMD", expiry: "20260918", strike: 210, right: "C" })]: legQuote({ symbol: "AMD", bid: 2, ask: 2.4, timestamp: FRESH }),
    };
    const natural = resolveNaturalSpreadQuote("AMD", position, prices)!;
    expect(natural.asOf).toBe(STALE);
  });

  it("resolveOrderPriceData stamps a BAG with the oldest leg timestamp, not now", () => {
    const order = {
      orderId: 1,
      permId: 1,
      action: "BUY",
      totalQuantity: 1,
      limitPrice: null,
      contract: {
        symbol: "AMD",
        secType: "BAG",
        comboLegs: [
          { symbol: "AMD", strike: 200, right: "C", expiry: "20260918", ratio: 1, action: "BUY" },
          { symbol: "AMD", strike: 210, right: "C", expiry: "20260918", ratio: 1, action: "SELL" },
        ],
      },
    } as unknown as OpenOrder;
    const prices: Record<string, PriceData> = {
      [optionKey({ symbol: "AMD", expiry: "20260918", strike: 200, right: "C" })]: legQuote({ symbol: "AMD", bid: 5, ask: 5.4, last: 5.2, timestamp: STALE }),
      [optionKey({ symbol: "AMD", expiry: "20260918", strike: 210, right: "C" })]: legQuote({ symbol: "AMD", bid: 2, ask: 2.4, last: 2.2, timestamp: FRESH }),
    };
    const quote = resolveOrderPriceData(order, prices)!;
    expect(quote.timestamp).toBe(STALE);
    expect(buildQuoteTelemetryModel(quote, null, NOW)!.last.label).toBe("CLOSE");
  });
});
