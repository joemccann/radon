import { describe, expect, it } from "vitest";

import { formatHoldDuration, isEarlierLocalDay } from "@/lib/holdTime";
import { syncNewTrades } from "@/lib/journalImport";
import { resolveMarketValue } from "@/lib/positionUtils";
import { evaluateRelayTick } from "@/lib/probeFreshness";
import type { PortfolioPosition } from "@/lib/types";
import { yahooResultToPrice } from "@/lib/yahooQuote";

describe("web correctness security remediations", () => {
  it("rejects impossible date-only values instead of normalizing them", () => {
    expect(isEarlierLocalDay("2026-02-31", "2026-03-10")).toBe(false);
    expect(formatHoldDuration("2026-02-31", "2026-03-10")).toBeNull();
  });

  it("subtracts commission from imported sell proceeds", () => {
    const result = syncNewTrades([], [{
      symbol: "AAPL",
      date: "2026-08-13",
      action: "SELL",
      net_quantity: -10,
      avg_price: 100,
      commission: 5,
      realized_pnl: 0,
      sec_type: "STK",
      ib_exec_id: "sell-1",
    }]);
    expect(result.trades[0]?.total_cost).toBe(995);
  });

  it("does not publish a partial multi-leg market value", () => {
    const position = {
      legs: [
        { direction: "LONG", market_value: 500 },
        { direction: "SHORT", market_value: null },
      ],
    } as unknown as PortfolioPosition;
    expect(resolveMarketValue(position)).toBeNull();
  });

  it("does not treat an implausible future relay tick as fresh", () => {
    const now = Date.parse("2026-08-13T15:00:00Z");
    const result = evaluateRelayTick({
      state: "ok",
      updated_at: new Date(now).toISOString(),
      last_error: JSON.stringify({
        active_subscriptions: 1,
        last_tick_at: new Date(now + 5 * 60_000).toISOString(),
      }),
    }, "open", now);
    expect(result).toMatchObject({ applicable: true, fresh: false, age_secs: null });
  });

  it("aggregates intraday Yahoo candles into session OHLCV", () => {
    const price = yahooResultToPrice("ES", {
      chart: {
        result: [{
          timestamp: [1, 2, 3],
          indicators: { quote: [{
            open: [100, 105, 103],
            high: [110, 108, 115],
            low: [95, 99, 97],
            close: [104, 106, 112],
            volume: [10, 20, 30],
          }] },
        }],
      },
    });
    expect(price).toMatchObject({ last: 112, open: 100, high: 115, low: 95, volume: 60 });
  });
});
