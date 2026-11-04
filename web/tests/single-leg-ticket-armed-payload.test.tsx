/**
 * @vitest-environment jsdom
 *
 * SingleLegOrderTicket arms for the reviewed payload. The Paper/Live toggle
 * sits inside the confirm step, so the destination is part of what was
 * reviewed: flipping it must disarm, or the next click sends a reviewed paper
 * order to the live broker. TIF is part of the payload too. And a parent
 * refresh that only re-prices the held leg must not overwrite a partial-close
 * quantity the operator typed.
 */

import React, { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import SingleLegOrderTicket from "../components/SingleLegOrderTicket";
import InstrumentDetailModal from "../components/InstrumentDetailModal";
import type { LinearOrderRiskInput } from "@/lib/order";
import type { PortfolioData, PortfolioLeg, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

vi.mock("@/lib/RealtimePricesContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/RealtimePricesContext")>();
  return {
    ...actual,
    useRealtimePrices: () => ({ ...actual.useRealtimePrices(), connected: true, ibConnected: true }),
  };
});

vi.mock("../components/Modal", () => ({
  default: ({ children, title }: { children: React.ReactNode; title: string }) => (
    <div role="dialog" aria-label={title}>
      {children}
    </div>
  ),
}));

function price(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol, last: (bid + ask) / 2, lastIsCalculated: false, bid, ask,
    bidSize: 1, askSize: 1, volume: 1, high: null, low: null, open: null, close: null,
    week52High: null, week52Low: null, avgVolume: null, delta: null, gamma: null,
    theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: new Date().toISOString(),
  };
}

type SentRequest = { url: string; method: string; body: unknown };
const sent: SentRequest[] = [];

beforeEach(() => {
  sent.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (method === "POST") {
        sent.push({ url: String(input), method, body: JSON.parse(String(init?.body)) });
      }
      return new Response(JSON.stringify({ status: "Submitted" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const EMPTY_PORTFOLIO = { positions: [] } as unknown as PortfolioData;

/** The BookTab stock-ticket wiring: Paper flips placeUrl + payload shape. */
function PaperStockTicket() {
  const [paperMode, setPaperMode] = useState(false);
  const [quantity, setQuantity] = useState("10");
  const [limitPrice, setLimitPrice] = useState("100");
  const qty = parseInt(quantity, 10);
  const px = parseFloat(limitPrice);
  const riskInput: LinearOrderRiskInput | null =
    qty > 0 && px > 0
      ? {
          type: "linear",
          ticker: "AAPL",
          instrument: "stock",
          action: "BUY",
          quantity: qty,
          limitPrice: px,
          multiplier: 1,
          heldQuantity: 0,
          heldShortQuantity: 0,
          description: `BUY ${qty} AAPL`,
        }
      : null;
  return (
    <SingleLegOrderTicket
      defaultAction="BUY"
      defaultTif="DAY"
      quantity={quantity}
      onQuantityChange={setQuantity}
      quantityPlaceholder="Shares"
      bid={99}
      mid={100}
      ask={101}
      priceData={price("AAPL", 99, 101)}
      feedConnected
      isValid={qty > 0 && px > 0}
      limitPrice={limitPrice}
      onLimitPriceChange={setLimitPrice}
      riskInput={riskInput}
      portfolio={EMPTY_PORTFOLIO}
      riskSurface="test-paper"
      riskPaperMode={paperMode}
      onRiskPaperModeChange={setPaperMode}
      placeUrl={paperMode ? "/api/paper/place" : "/api/orders/place"}
      buildPayload={({ action, quantity, limitPrice, tif }) =>
        paperMode
          ? { ticker: "AAPL", side: action, quantity, limitPrice }
          : { type: "stock", symbol: "AAPL", action, quantity, limitPrice, tif }
      }
      buildSuccessMessage={() => "placed"}
    />
  );
}

const confirmButton = () => screen.queryByRole("button", { name: /confirm order/i }) as HTMLButtonElement | null;

describe("SingleLegOrderTicket arms for the reviewed payload", () => {
  it("transmits the reviewed live order, and nothing before confirm", async () => {
    render(<PaperStockTicket />);
    fireEvent.click(screen.getByRole("button", { name: /place order/i }));
    expect(sent).toHaveLength(0);
    expect(confirmButton()?.disabled).toBe(false);

    await act(async () => {
      fireEvent.click(confirmButton()!);
    });
    expect(sent).toEqual([
      {
        url: "/api/orders/place",
        method: "POST",
        body: { type: "stock", symbol: "AAPL", action: "BUY", quantity: 10, limitPrice: 100, tif: "DAY" },
      },
    ]);
  });

  it("flipping Live to Paper on the confirm step disarms", async () => {
    render(<PaperStockTicket />);
    fireEvent.click(screen.getByRole("button", { name: /place order/i }));
    expect(confirmButton()).toBeTruthy();

    fireEvent.click(screen.getByTestId("paper-mode-toggle"));
    expect(confirmButton()).toBeNull();
    expect(sent).toHaveLength(0);

    // Re-review lands on the new destination.
    fireEvent.click(screen.getByRole("button", { name: /place order/i }));
    expect(screen.getByTestId("paper-mode-toggle").getAttribute("aria-pressed")).toBe("true");
    await act(async () => {
      fireEvent.click(confirmButton()!);
    });
    expect(sent).toEqual([
      {
        url: "/api/paper/place",
        method: "POST",
        body: { ticker: "AAPL", side: "BUY", quantity: 10, limitPrice: 100 },
      },
    ]);
  });

  it("flipping Paper back to Live on the confirm step disarms", () => {
    render(<PaperStockTicket />);
    fireEvent.click(screen.getByRole("button", { name: /place order/i }));
    fireEvent.click(screen.getByTestId("paper-mode-toggle"));
    fireEvent.click(screen.getByRole("button", { name: /place order/i }));
    expect(confirmButton()).toBeTruthy();
    fireEvent.click(screen.getByTestId("paper-mode-toggle"));
    expect(confirmButton()).toBeNull();
    expect(sent).toHaveLength(0);
  });

  it("changing TIF on the confirm step disarms", () => {
    render(<PaperStockTicket />);
    fireEvent.click(screen.getByRole("button", { name: /place order/i }));
    expect(confirmButton()).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "GTC" }));
    expect(confirmButton()).toBeNull();
    expect(sent).toHaveLength(0);
  });
});

const HELD_SHORT_CALL: PortfolioLeg = {
  con_id: 777,
  direction: "SHORT",
  contracts: 5,
  type: "Call",
  strike: 1250,
  entry_cost: -6_830,
  avg_cost: -1_366,
  market_price: 0.05,
  market_price_is_calculated: false,
  market_value: -25,
};

const PORTFOLIO: PortfolioData = {
  bankroll: 100_000,
  positions: [
    {
      id: 42,
      ticker: "MU",
      structure: "Short Call $1250.0",
      structure_type: "Short Call",
      risk_profile: "undefined",
      direction: "SHORT",
      contracts: 5,
      expiry: "2026-06-26",
      entry_date: "2026-06-01",
      entry_cost: -6_830,
      market_value: -25,
      max_risk: null,
      kelly_optimal: null,
      target: null,
      stop: null,
      legs: [HELD_SHORT_CALL],
    } as unknown as PortfolioPosition,
  ],
  account_summary: { available_funds: 500_000, buying_power: 1_000_000 },
} as unknown as PortfolioData;

const PRICES: Record<string, PriceData> = {
  MU_20260626_1250_C: price("MU_20260626_1250_C", 0.01, 0.09),
};

function modal(leg: PortfolioLeg) {
  return (
    <InstrumentDetailModal
      leg={leg}
      ticker="MU"
      expiry="2026-06-26"
      prices={PRICES}
      portfolio={PORTFOLIO}
      onClose={() => {}}
    />
  );
}

const qtyInput = () => screen.getByPlaceholderText("Contracts") as HTMLInputElement;

describe("InstrumentDetailModal keeps a partial-close quantity across price refreshes", () => {
  it("a re-priced copy of the same held leg does not reset the typed quantity", () => {
    const { rerender } = render(modal(HELD_SHORT_CALL));
    expect(qtyInput().value).toBe("5");
    fireEvent.change(qtyInput(), { target: { value: "2" } });

    // The portfolio poll hands a fresh object for the same contract.
    rerender(modal({ ...HELD_SHORT_CALL, market_price: 0.06, market_value: -30 }));
    expect(qtyInput().value).toBe("2");
  });

  it("a broker change to the held size while armed resets the quantity and disarms", () => {
    const { rerender } = render(modal(HELD_SHORT_CALL));
    fireEvent.change(qtyInput(), { target: { value: "2" } });
    fireEvent.change(screen.getByPlaceholderText("0.00"), { target: { value: "0.05" } });
    fireEvent.click(screen.getByRole("button", { name: /place order/i }));
    expect(confirmButton()).toBeTruthy();

    rerender(modal({ ...HELD_SHORT_CALL, contracts: 3 }));
    expect(qtyInput().value).toBe("3");
    expect(confirmButton()).toBeNull();
    expect(sent).toHaveLength(0);
  });
});
