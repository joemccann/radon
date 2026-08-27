// @vitest-environment jsdom
//
// CBRS 2026-08-27: selling 40× $182.5 puts at a $4 limit credits $16,000.
// The ticket showed MAX GAIN $12,248 because the F1 round-trip (quoted
// half-spread on entry + estimated exit) was subtracted from the limit credit.
// MAX GAIN for a short put is the credit at the limit.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";

import MobileOrderTicket from "@/components/mobile/MobileOrderTicket";
import type { PriceData } from "@/lib/pricesProtocol";
import type { PortfolioData } from "@/lib/types";

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({ pushNotification: vi.fn() }),
  useOrderActionsOptional: () => ({ pushNotification: vi.fn() }),
}));

const PUT_KEY = "CBRS_20260828_182.5_P";

function quote(symbol: string, bid: number, ask: number, extra: Partial<PriceData> = {}): PriceData {
  return {
    symbol,
    last: (bid + ask) / 2,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 10,
    askSize: 10,
    volume: 2636,
    high: ask,
    low: bid,
    open: bid,
    close: bid,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: new Date().toISOString(),
    ...extra,
  } as PriceData;
}

const PRICES: Record<string, PriceData> = {
  CBRS: quote("CBRS", 180, 181, { last: 180.5 }),
  [PUT_KEY]: quote(PUT_KEY, 2.5, 4.3, { last: 3.2, high: 5, low: 2.15, volume: 2636 }),
};

const PORTFOLIO = {
  bankroll: 1_123_559,
  peak_value: 1_123_559,
  last_sync: new Date().toISOString(),
  total_deployed_pct: 0,
  total_deployed_dollars: 0,
  remaining_capacity_pct: 100,
  position_count: 0,
  defined_risk_count: 0,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  positions: [],
  account_summary: {
    net_liquidation: 1_123_559,
    excess_liquidity: 1_123_559,
  },
} as unknown as PortfolioData;

function cell(label: string): string {
  const node = [...document.querySelectorAll(".ticket-risk-cell")].find(
    (el) => (el.querySelector(".ticket-risk-cell-label")?.textContent ?? "").trim() === label,
  );
  if (!node) throw new Error(`no risk cell labelled ${label}`);
  return (node.querySelector(".ticket-risk-cell-value")?.textContent ?? "").trim();
}

afterEach(cleanup);

describe("short put ticket: max gain is the limit credit", () => {
  it("40× $182.5 put sold at $4 shows $16,000 max gain, not the $12,248 spread haircut", async () => {
    render(
      <MobileOrderTicket
        open
        ticker="CBRS"
        legs={[
          {
            id: PUT_KEY,
            action: "SELL",
            right: "P",
            strike: 182.5,
            expiry: "20260828",
            quantity: 40,
            limitPrice: 4,
          },
        ]}
        prices={PRICES}
        spot={180.5}
        portfolio={PORTFOLIO}
        onClose={vi.fn()}
      />,
    );

    await waitFor(() => expect(document.querySelector(".ticket-risk")).toBeTruthy());

    const limit = document.querySelector<HTMLInputElement>("[data-testid='mobile-order-ticket-price-input']");
    expect(limit).toBeTruthy();
    fireEvent.change(limit!, { target: { value: "4" } });

    await waitFor(() => {
      expect(cell("MAX GAIN")).toBe("$16,000.00");
    });
    expect(cell("MAX LOSS")).toBe("$714,000.00");
    expect(document.querySelector(".ticket-risk-total")?.textContent).toMatch(/\$16,000\.00\s*CR/);
  });
});
