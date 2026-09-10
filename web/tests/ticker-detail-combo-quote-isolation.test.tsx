/** @vitest-environment jsdom */
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioData } from "@/lib/types";

const seen = vi.hoisted(() => ({ props: {} as Record<string, unknown> }));
vi.mock("@/lib/useStockState", () => ({ useStockState: () => ({ data: null, loading: false }) }));
vi.mock("@/components/ticker-detail/AssetCockpit", () => ({
  default: (props: Record<string, unknown>) => {
    seen.props = props;
    return <button onClick={() => (props.onInstrumentViewChange as (view: string) => void)("underlying")}>Stock</button>;
  },
}));
import TickerDetailContent from "@/components/TickerDetailContent";

function quote(symbol: string, bid: number, ask: number): PriceData {
  return { symbol, bid, ask, last: (bid + ask) / 2, bidSize: 10, askSize: 10, close: null, timestamp: "2026-09-05T18:00:00Z" } as PriceData;
}
const portfolio = {
  last_sync: "2026-09-05T18:00:00Z",
  positions: [{
    id: 12, ticker: "IWM", structure: "Risk Reversal", structure_type: "Risk Reversal", expiry: "2026-10-05", contracts: 50,
    direction: "COMBO", entry_cost: -579.79,
    legs: [
      { direction: "LONG", contracts: 50, type: "Call", strike: 247, entry_cost: 17_285.02 },
      { direction: "SHORT", contracts: 50, type: "Put", strike: 243, entry_cost: 17_864.81 },
    ],
  }],
} as PortfolioData;
const stock = quote("IWM", 244.64, 244.66);
const call = quote("IWM_20261005_247_C", 3.6, 3.7);
const put = quote("IWM_20261005_243_P", 3.8, 3.9);

function renderCockpit(prices: Record<string, PriceData>) {
  return render(<TickerDetailContent ticker="IWM" positionId={12} activeTab="book" onTabChange={() => {}} prices={prices} fundamentals={{}} portfolio={portfolio} orders={null} theme="light" />);
}
afterEach(cleanup);

describe("implied spread quote isolation", () => {
  it.each([{}, { [call.symbol]: call }])("does not present the underlying as a combo premium when a leg quote is missing", (legs) => {
    renderCockpit({ IWM: stock, ...legs });
    expect(seen.props.bookKind).toBe("combo");
    expect(seen.props.bookPriceData).toBeNull();
    expect(seen.props.quotePriceData).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Stock" }));
    expect(seen.props.bookKind).toBe("stock");
    expect(seen.props.bookPriceData).toBe(stock);
  });

  it("preserves the signed spread quote when both executable leg quotes are available", () => {
    renderCockpit({ IWM: stock, [call.symbol]: call, [put.symbol]: put });
    expect(seen.props.bookKind).toBe("combo");
    expect(seen.props.isSpreadNet).toBe(true);
    const spread = seen.props.bookPriceData as PriceData;
    expect(spread.bid).toBeCloseTo(-0.3);
    expect(spread.ask).toBeCloseTo(-0.1);
    expect(spread.last).toBeCloseTo(-0.2);
  });
});
