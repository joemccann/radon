/**
 * @vitest-environment jsdom
 *
 * Live Price Trace: a multi-leg SPREAD NET is not a raw price.
 *
 * For a multi-leg position (e.g. a reverse risk reversal) the ticker page
 * resolves a NET spread price that can legitimately be a CREDIT (negative)
 * per the Sign Convention (credits negative, debits positive). Plotting that
 * negative net as a bare "$-81.60" with a profit-green pill reads like a data
 * error. PriceChart's `valueKind="spread-net"` branch labels it honestly:
 *
 *   - badge reads NET CREDIT (value < 0) / NET DEBIT (value > 0), not MIDPRICE
 *   - value formats as "($81.60) cr" / "$81.60 db", sign preserved
 *   - the series color is brand-neutral, NOT the profit-green/fault-red that a
 *     raw price uses vs PREV CLOSE
 *
 * A normal price chart (`valueKind="price"`, the default) is unchanged.
 */

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import PriceChart from "../components/PriceChart";
import type { PriceData } from "@/lib/pricesProtocol";
import { resolveChartPrice } from "@/lib/usePriceHistory";

// Capture the props handed to <Liveline> so we can assert color + formatValue
// without exercising the canvas renderer.
let lastLivelineProps: Record<string, unknown> = {};
vi.mock("liveline", () => ({
  Liveline: (props: Record<string, unknown>) => {
    lastLivelineProps = props;
    const formatValue = props.formatValue as ((v: number) => string) | undefined;
    const value = props.value as number;
    return (
      <div data-testid="liveline" data-color={String(props.color)}>
        {formatValue ? formatValue(value) : String(value)}
      </div>
    );
  },
}));

// Deterministic chart value; bypass the live-walk / WS seeding logic.
const mockUsePriceHistory = vi.fn();
vi.mock("@/lib/usePriceHistory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/usePriceHistory")>();
  return {
    ...actual,
    usePriceHistory: (...args: unknown[]) => mockUsePriceHistory(...args),
  };
});

// Distinct, resolvable color tokens per role so we can tell them apart.
// Partial mock: ChartPanel still needs the real chartFamilyLabel/etc.
vi.mock("@/lib/chartSystem", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/chartSystem")>();
  return {
    ...actual,
    resolveChartSeriesColor: (role: string) => `color-${role}`,
  };
});

afterEach(() => {
  cleanup();
  lastLivelineProps = {};
  mockUsePriceHistory.mockReset();
});

function priceData(partial: Partial<PriceData>): PriceData {
  return {
    symbol: "MU",
    last: null,
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
    ...partial,
  } as PriceData;
}

function stubHistory(value: number, overrides: Record<string, unknown> = {}) {
  mockUsePriceHistory.mockReturnValue({
    data: [{ time: 1, value }],
    value,
    loading: false,
    isMid: false,
    isCalculated: false,
    ...overrides,
  });
}

// ─── spread net (negative → credit) ───

test("[spread-net] negative net renders a NET CREDIT badge, not MIDPRICE", () => {
  stubHistory(-81.6, { isMid: true });
  render(
    <PriceChart
      ticker="MU"
      prices={{}}
      priceData={priceData({ last: -81.6 })}
      valueKind="spread-net"
    />,
  );
  expect(screen.getByText("NET CREDIT")).toBeTruthy();
  expect(screen.queryByText("MIDPRICE")).toBeNull();
  expect(screen.queryByText("MARK")).toBeNull();
});

test("[spread-net] accepts an unmocked signed last while price mode rejects it", () => {
  const data = priceData({ last: -81.6, bid: null, ask: null });
  expect(resolveChartPrice(data, "spread-net").price).toBe(-81.6);
  expect(resolveChartPrice(data, "price").price).toBeNull();
});

test("[spread-net] negative net formats as a signed credit, not a bare $-81.60", () => {
  stubHistory(-81.6);
  render(
    <PriceChart ticker="MU" prices={{}} priceData={priceData({ last: -81.6 })} valueKind="spread-net" />,
  );
  expect(screen.getByTestId("liveline").textContent).toBe("($81.60) cr");
  expect(screen.queryByText("$-81.60")).toBeNull();
});

test("[spread-net] negative net does NOT use the profit-green or fault-red series color", () => {
  // close below value would otherwise pick profit-green; a bare negative would
  // otherwise pick fault-red. A spread net must be brand-neutral regardless.
  stubHistory(-81.6);
  render(
    <PriceChart
      ticker="MU"
      prices={{}}
      priceData={priceData({ last: -81.6, close: 100 })}
      valueKind="spread-net"
    />,
  );
  expect(lastLivelineProps.color).toBe("color-neutral");
  expect(lastLivelineProps.color).not.toBe("color-primary");
  expect(lastLivelineProps.color).not.toBe("color-fault");
});

test("[spread-net] positive net renders NET DEBIT and formats as a debit", () => {
  stubHistory(2.45);
  render(
    <PriceChart ticker="MU" prices={{}} priceData={priceData({ last: 2.45 })} valueKind="spread-net" />,
  );
  expect(screen.getByText("NET DEBIT")).toBeTruthy();
  expect(screen.getByTestId("liveline").textContent).toBe("$2.45 db");
});

test("[spread-net] a spread net shows no PREV CLOSE reference line", () => {
  stubHistory(-81.6);
  render(
    <PriceChart
      ticker="MU"
      prices={{}}
      priceData={priceData({ last: -81.6, close: 100 })}
      valueKind="spread-net"
    />,
  );
  expect(lastLivelineProps.referenceLine).toBeUndefined();
});

// ─── normal price chart is unchanged ───

test("[price] a normal mid-price chart still renders MIDPRICE and $X.XX", () => {
  stubHistory(98.42, { isMid: true });
  render(<PriceChart ticker="MU" prices={{}} priceData={priceData({ bid: 98.4, ask: 98.44 })} />);
  expect(screen.getByText("MIDPRICE")).toBeTruthy();
  expect(screen.getByTestId("liveline").textContent).toBe("$98.42");
});

test("[price] a normal price above PREV CLOSE uses the profit-green series color", () => {
  stubHistory(105);
  render(<PriceChart ticker="MU" prices={{}} priceData={priceData({ last: 105, close: 100 })} />);
  expect(lastLivelineProps.color).toBe("color-primary");
  expect(lastLivelineProps.referenceLine).toEqual({ value: 100, label: "PREV CLOSE" });
});

test("[price] a normal price below PREV CLOSE uses the fault-red series color", () => {
  stubHistory(95);
  render(<PriceChart ticker="MU" prices={{}} priceData={priceData({ last: 95, close: 100 })} />);
  expect(lastLivelineProps.color).toBe("color-fault");
});
