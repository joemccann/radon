/**
 * @vitest-environment jsdom
 *
 * Listed-contract order surfaces (futures + index options) must show the same
 * nine-field quote telemetry the portfolio position drawer shows, so the
 * operator never enters an order against less information than they get when
 * reviewing a held position.
 *
 * `ListedContractOrderForm` is the single chokepoint both adapters render
 * through, so the panel is wired once there and threaded from the adapter that
 * can name the traded contract's price key.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PriceData } from "@/lib/pricesProtocol";

const mocks = vi.hoisted(() => ({
  prices: {} as Record<string, PriceData>,
  indexHook: vi.fn(),
}));

vi.mock("@/lib/TickerDetailContext", () => ({
  useTickerDetailOptional: () => ({ getPrices: () => mocks.prices }),
}));
vi.mock("@/lib/useIndexOptionsChain", () => ({
  useIndexOptionsChain: (symbol: string, expiry: string | null) => mocks.indexHook(symbol, expiry),
}));
vi.mock("@/lib/useFuturesChain", () => ({
  useFuturesChain: () => ({
    data: {
      symbol: "VIX",
      exchange: "CFE",
      count: 1,
      contracts: [
        {
          conId: 9001,
          symbol: "VIX",
          localSymbol: "VIXU6",
          exchange: "CFE",
          currency: "USD",
          lastTradeDateOrContractMonth: "20260916",
          multiplier: "1000",
          tradingClass: "VX",
          marketName: "VIX",
          minTick: 0.05,
        },
      ],
    },
    loading: false,
    error: null,
  }),
}));

import { ListedContractOrderForm } from "../components/ticker-detail/ListedContractOrderForm";
import { IndexOptionOrderForm } from "../components/ticker-detail/IndexOptionOrderForm";
import { FuturesOrderForm } from "../components/ticker-detail/FuturesOrderForm";

const NINE_LABELS = ["BID", "MID", "ASK", "SPREAD", "VOLUME", "HIGH", "LOW", "DAY"];

function makePriceData(overrides: Partial<PriceData> & { symbol: string }): PriceData {
  return {
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    timestamp: new Date().toISOString(),
    ...overrides,
  } as PriceData;
}

function labelsIn(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".price-bar-label")].map((n) => n.textContent ?? "");
}

function renderChokepoint(props: Partial<React.ComponentProps<typeof ListedContractOrderForm>> = {}) {
  return render(
    <ListedContractOrderForm
      eyebrow={<>VIX Options</>}
      contractSelector={null}
      multiplier={100}
      multiplierDisplay="100"
      notionalLabel="Notional"
      limitPriceLabel="Limit Price"
      limitPriceStep={0.05}
      buildRiskInput={() => null}
      portfolio={null}
      surface="test-surface"
      buildSubmit={() => ({ error: "not submittable" })}
      submitLabel="BUY"
      submitDisabled
      {...props}
    />,
  );
}

const VIX_CALL_QUOTE = makePriceData({
  symbol: "VIX_20260916_20_C",
  bid: 1.85,
  ask: 2.05,
  last: 1.95,
  close: 1.7,
  volume: 812,
  high: 2.2,
  low: 1.6,
});

afterEach(() => {
  cleanup();
  mocks.indexHook.mockReset();
  mocks.prices = {};
});

describe("listed-contract order quote telemetry", () => {
  it("renders all nine fields plus the instrument label on the shared chokepoint", () => {
    const { container } = renderChokepoint({
      priceData: VIX_CALL_QUOTE,
      quoteLabel: "VIX 2026-09-16 $20 C",
    });

    const labels = labelsIn(container);
    for (const label of NINE_LABELS) {
      expect(labels).toContain(label);
    }
    expect(labels).toContain("LAST");
    expect(labels).toContain("VIX 2026-09-16 $20 C");
    expect(container.querySelectorAll(".price-bar-item").length).toBe(10);
  });

  it("renders the honest empty state when the surface has no quote for the contract", () => {
    const { container } = renderChokepoint();

    expect(container.querySelector(".price-bar-empty")?.textContent).toBe("No real-time data");
    expect(labelsIn(container)).toHaveLength(0);
  });

  it("keeps the quote block above the contract selector and the order inputs", () => {
    const { container } = renderChokepoint({
      priceData: VIX_CALL_QUOTE,
      contractSelector: <div data-testid="contract-selector" />,
    });

    const form = container.querySelector("form.futures-order-form") as HTMLElement;
    const bar = form.querySelector(".price-bar") as HTMLElement;
    const selector = form.querySelector("[data-testid='contract-selector']") as HTMLElement;
    expect(bar.compareDocumentPosition(selector) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

function strikeSelect(): HTMLSelectElement {
  const selects = screen.getAllByRole("combobox") as HTMLSelectElement[];
  const strike = selects.find((s) => [...s.options].some((o) => o.value === "7001"));
  if (!strike) throw new Error("strike select not found");
  return strike;
}

describe("IndexOptionOrderForm quote threading", () => {
  beforeEach(() => {
    mocks.indexHook.mockImplementation((symbol: string, expiry: string | null) =>
      expiry == null
        ? {
            data: {
              symbol,
              exchange: "CBOE",
              tradingClass: "VIX",
              expirations: ["20260916"],
              contracts: [],
              count: 0,
            },
            loading: false,
            error: null,
          }
        : {
            data: {
              symbol,
              contracts: [
                {
                  conId: 7001,
                  symbol,
                  localSymbol: "VIX   260916C00020000",
                  exchange: "CBOE",
                  currency: "USD",
                  lastTradeDateOrContractMonth: "20260916",
                  strike: 20,
                  right: "C",
                  multiplier: "100",
                  tradingClass: "VIX",
                  minTick: 0.05,
                },
              ],
              count: 1,
            },
            loading: false,
            error: null,
          },
    );
  });

  it("shows the selected strike's own quote, not the cash index", async () => {
    mocks.prices = {
      VIX: makePriceData({ symbol: "VIX", bid: 15.1, ask: 15.3, last: 15.2 }),
      VIX_20260916_20_C: VIX_CALL_QUOTE,
    };

    const { container } = render(<IndexOptionOrderForm ticker="VIX" portfolio={null} />);
    fireEvent.change(strikeSelect(), { target: { value: "7001" } });

    await waitFor(() => {
      expect(labelsIn(container)).toContain("VIX 2026-09-16 $20 C");
    });
    const values = [...container.querySelectorAll(".price-bar-value")].map((n) => n.textContent ?? "");
    expect(values).toContain("$1.85");
    expect(values).not.toContain("$15.10");
  });

  it("falls back to the empty state while no strike is selected", () => {
    mocks.prices = { VIX_20260916_20_C: VIX_CALL_QUOTE };

    const { container } = render(<IndexOptionOrderForm ticker="VIX" portfolio={null} />);

    expect(container.querySelector(".price-bar-empty")?.textContent).toBe("No real-time data");
  });
});

describe("FuturesOrderForm quote threading", () => {
  it("renders exactly one quote panel, labelled as the index the chain is listed against", () => {
    const { container } = render(
      <FuturesOrderForm
        ticker="VIX"
        portfolio={null}
        priceData={makePriceData({ symbol: "VIX", bid: 15.1, ask: 15.3, last: 15.2, close: 14.9 })}
      />,
    );

    expect(container.querySelectorAll(".price-bar").length).toBe(1);
    expect(labelsIn(container)).toContain("VIX Index");
    for (const label of NINE_LABELS) {
      expect(labelsIn(container)).toContain(label);
    }
  });
});
