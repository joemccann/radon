import { describe, expect, it } from "vitest";
import { buildGroupedComboModifyTarget } from "@/lib/openOrderComboModify";
import type { OpenOrderComboRow } from "@/lib/openOrderCombos";
import { normalizeComboLegs, resolveOrderPriceData } from "@/components/ModifyOrderModal";
import type { PriceData } from "@/lib/pricesProtocol";

describe("buildGroupedComboModifyTarget", () => {
  it("converts grouped OPT orders into a synthetic BAG modify target", () => {
    const row: OpenOrderComboRow = {
      kind: "combo",
      id: "combo-aapl",
      index: 0,
      symbol: "AAPL",
      structure: "Risk Reversal",
      summary: "Short Put 150 / Long Call 165",
      orders: [
        {
          orderId: 1001,
          permId: 9001,
          symbol: "AAPL P150",
          contract: {
            conId: 12001,
            symbol: "AAPL",
            secType: "OPT",
            strike: 150,
            right: "P",
            expiry: "2026-04-17",
          },
          action: "SELL",
          orderType: "LMT",
          totalQuantity: 10,
          limitPrice: 0.95,
          auxPrice: null,
          status: "Submitted",
          filled: 0,
          remaining: 10,
          avgFillPrice: null,
          tif: "DAY",
        },
        {
          orderId: 1002,
          permId: 9002,
          symbol: "AAPL C165",
          contract: {
            conId: 12002,
            symbol: "AAPL",
            secType: "OPT",
            strike: 165,
            right: "C",
            expiry: "2026-04-17",
          },
          action: "BUY",
          orderType: "LMT",
          totalQuantity: 10,
          limitPrice: 1.15,
          auxPrice: null,
          status: "Submitted",
          filled: 0,
          remaining: 10,
          avgFillPrice: null,
          tif: "DAY",
        },
      ],
      totalQuantity: 10,
      orderType: "Risk Reversal",
      status: "Submitted",
      tif: "DAY",
      limitPrice: null,
    };

    const target = buildGroupedComboModifyTarget(row);
    expect(target.cancelOrders).toEqual([
      { orderId: 1001, permId: 9001 },
      { orderId: 1002, permId: 9002 },
    ]);
    expect(target.modalOrder).toMatchObject({
      symbol: "AAPL",
      action: "BUY",
      orderType: "LMT",
      totalQuantity: 10,
      contract: {
        symbol: "AAPL",
        secType: "BAG",
        comboLegs: [
          { conId: 12001, ratio: 1, action: "SELL", strike: 150, right: "P", expiry: "2026-04-17" },
          { conId: 12002, ratio: 1, action: "BUY", strike: 165, right: "C", expiry: "2026-04-17" },
        ],
      },
    });
  });

  it("normalizes grouped ratio legs to base quantity plus ratios", () => {
    const row = {
      kind: "combo",
      id: "combo-aaoi",
      index: 0,
      symbol: "AAOI",
      structure: "Risk Reversal",
      summary: "Short Put 85 / Long Call 90",
      orders: [
        {
          orderId: 2001,
          permId: 9101,
          symbol: "AAOI P85",
          contract: { conId: 22001, symbol: "AAOI", secType: "OPT", strike: 85, right: "P", expiry: "2026-04-17" },
          action: "SELL",
          orderType: "LMT",
          totalQuantity: 25,
          limitPrice: 2,
          auxPrice: null,
          status: "Submitted",
          filled: 0,
          remaining: 25,
          avgFillPrice: null,
          tif: "DAY",
        },
        {
          orderId: 2002,
          permId: 9102,
          symbol: "AAOI C90",
          contract: { conId: 22002, symbol: "AAOI", secType: "OPT", strike: 90, right: "C", expiry: "2026-04-17" },
          action: "BUY",
          orderType: "LMT",
          totalQuantity: 50,
          limitPrice: 4,
          auxPrice: null,
          status: "Submitted",
          filled: 0,
          remaining: 50,
          avgFillPrice: null,
          tif: "DAY",
        },
      ],
      totalQuantity: 25,
      orderType: "Risk Reversal",
      status: "Submitted",
      tif: "DAY",
      limitPrice: null,
    } satisfies OpenOrderComboRow;

    const target = buildGroupedComboModifyTarget(row);
    expect(target.modalOrder.totalQuantity).toBe(25);
    expect(target.modalOrder.contract.comboLegs).toEqual([
      { conId: 22001, ratio: 1, action: "SELL", symbol: "AAOI", strike: 85, right: "P", expiry: "2026-04-17" },
      { conId: 22002, ratio: 2, action: "BUY", symbol: "AAOI", strike: 90, right: "C", expiry: "2026-04-17" },
    ]);
  });

  it("edited ratio legs drive reference and payload price", () => {
    const modalOrder = buildGroupedComboModifyTarget({
      kind: "combo",
      id: "combo-ratio",
      index: 0,
      symbol: "AAOI",
      structure: "Ratio",
      summary: "Ratio",
      orders: [
        { orderId: 1, permId: 1, symbol: "AAOI C90", contract: { conId: 1, symbol: "AAOI", secType: "OPT", strike: 90, right: "C", expiry: "2026-04-17" }, action: "BUY", orderType: "LMT", totalQuantity: 1, limitPrice: 4, auxPrice: null, status: "Submitted", filled: 0, remaining: 1, avgFillPrice: null, tif: "DAY" },
        { orderId: 2, permId: 2, symbol: "AAOI C100", contract: { conId: 2, symbol: "AAOI", secType: "OPT", strike: 100, right: "C", expiry: "2026-04-17" }, action: "SELL", orderType: "LMT", totalQuantity: 2, limitPrice: 1, auxPrice: null, status: "Submitted", filled: 0, remaining: 2, avgFillPrice: null, tif: "DAY" },
      ],
      totalQuantity: 1,
      orderType: "Ratio",
      status: "Submitted",
      tif: "DAY",
      limitPrice: 2,
    }).modalOrder;
    const pd = (symbol: string, bid: number, ask: number): PriceData => ({
      symbol, bid, ask, last: (bid + ask) / 2, lastIsCalculated: false,
      bidSize: null, askSize: null, volume: null, high: null, low: null, open: null,
      close: null, week52High: null, week52Low: null, avgVolume: null, delta: null,
      gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
      timestamp: new Date().toISOString(),
    });
    const edited = [
      { action: "BUY" as const, expiry: "2026-04-17", strike: "90", right: "C" as const, ratio: "1" },
      { action: "SELL" as const, expiry: "2026-04-17", strike: "100", right: "C" as const, ratio: "3" },
    ];
    const quote = resolveOrderPriceData(modalOrder, {
      AAOI_20260417_90_C: pd("AAOI_20260417_90_C", 4, 4.2),
      AAOI_20260417_100_C: pd("AAOI_20260417_100_C", 1, 1.1),
    }, null, edited);
    expect(quote?.bid).toBeCloseTo(0.7, 2);
    expect(quote?.ask).toBeCloseTo(1.2, 2);
    expect(normalizeComboLegs(edited)?.[1].ratio).toBe(3);
  });
});
