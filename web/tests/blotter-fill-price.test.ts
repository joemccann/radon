import { describe, expect, it } from "vitest";
import { blotterFillPrice, formatBlotterFillPrice } from "../lib/blotter/fillPrice";
import { journalRowsToBlotter } from "../lib/blotter/fromJournal";
import type { BlotterExecution } from "../lib/types";

const execution = (price: number | null, quantity = 1, side = "BOT"): BlotterExecution => ({
  exec_id: "fill", time: "2026-09-04T14:00:00Z", side, quantity, price,
  commission: 999, notional_value: 999999, net_cash_flow: -999999,
});

describe("historical fill price", () => {
  it.each([
    [null, "---"], [0, "$0.00"], [-1.25, "-$1.25"], [0.0123, "$0.0123"],
  ] as const)("formats %s without dropping premium precision", (price, formatted) => {
    expect(formatBlotterFillPrice(price)).toBe(formatted);
  });
  it("weights actual prices by executed quantity without commission or multiplier", () => {
    expect(blotterFillPrice({ executions: [execution(1.1, 1), execution(1.3, 3)] }).price).toBeCloseTo(1.25);
  });

  it.each([0, -1.25, 0.0123])("preserves a recorded price of %s", (price) => {
    expect(blotterFillPrice({ executions: [execution(price)] }).price).toBe(price);
  });

  it.each([null, NaN, Infinity])("does not silently omit an execution with price %s", (price) => {
    expect(blotterFillPrice({ executions: [execution(2), execution(price)] }).price).toBeNull();
  });

  it.each([0, -1, NaN, Infinity])("rejects invalid quantity %s", (quantity) => {
    expect(blotterFillPrice({ executions: [execution(2, quantity)] }).price).toBeNull();
  });

  it("does not manufacture a price for an empty execution list", () => {
    expect(blotterFillPrice({ executions: [] }).price).toBeNull();
  });

  it("marks buy/sell blends instead of calling them one execution", () => {
    expect(blotterFillPrice({ executions: [execution(1), execution(3, 1, "SLD")] }))
      .toEqual({ price: 2, aggregated: true });
  });

  it("preserves missing journal price as null rather than a false zero fill", () => {
    const result = journalRowsToBlotter([{ payload: { ticker: "VIX", action: "SELL_OPTION", contracts: 1, realized_pnl: 10 } }]);
    expect(result.closed_trades[0].executions[0].price).toBeNull();
  });

  it("marks a collapsed CLOSED journal record as aggregate", () => {
    const result = journalRowsToBlotter([{ payload: {
      ticker: "VIX", action: "CLOSED", contracts: 1000, fill_price: 1.23,
    } }]);
    expect(blotterFillPrice(result.closed_trades[0])).toEqual({ price: 1.23, aggregated: true });
  });

  it("marks rehydrated multi-fill records even when their net action is BUY", () => {
    const result = journalRowsToBlotter([{ payload: {
      ticker: "VIX", action: "BUY_OPTION", contracts: 5, fill_price: -0.5,
      notes: "Rehydrated from IB Flex Query on 2026-09-04", ib_exec_id: "a+b",
    } }]);
    expect(blotterFillPrice(result.open_trades[0])).toEqual({ price: -0.5, aggregated: true });
  });
});
