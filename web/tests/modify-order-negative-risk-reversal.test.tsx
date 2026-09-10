/**
 * @vitest-environment jsdom
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { OpenOrder, PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import ModifyOrderModal, { effectiveComboLegAction } from "../components/ModifyOrderModal";

vi.mock("@/lib/useRiskFreeRate", () => ({
  useRiskFreeRate: () => 0,
}));

vi.mock("../components/Modal", () => ({
  default: ({
    open,
    children,
    className,
  }: {
    open: boolean;
    children: React.ReactNode;
    className?: string;
  }) => (open ? React.createElement("div", { className: className ?? "mock-modal" }, children) : null),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// Window-relative expiry: a hardcoded date rots to T≈0 in CI, which zeroes the
// Black-Scholes implied value this test asserts on (tasks/lessons.md).
const EXPIRY = (() => {
  const d = new Date();
  d.setDate(d.getDate() + 180);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}${mm}${dd}`;
})();
const EXPIRY_DASHED = `${EXPIRY.slice(0, 4)}-${EXPIRY.slice(4, 6)}-${EXPIRY.slice(6)}`;

function priceData(overrides: Partial<PriceData> & { symbol: string }): PriceData {
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
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    // Fresh timestamp: the REL-236 quote-age gate disarms Modify on stale
    // leg quotes; this suite pins signed-limit behavior, not freshness.
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

const riskReversalOrder: OpenOrder = {
  orderId: 77,
  permId: 653611587,
  symbol: "MSFT Spread",
  contract: {
    conId: 28812380,
    symbol: "MSFT",
    secType: "BAG",
    strike: 0,
    right: "?",
    expiry: null,
    comboLegs: [
      { conId: 859556931, ratio: 1, action: "SELL", symbol: "MSFT", strike: 350, right: "P", expiry: EXPIRY_DASHED },
      { conId: 861002104, ratio: 1, action: "BUY", symbol: "MSFT", strike: 375, right: "C", expiry: EXPIRY_DASHED },
    ],
  },
  action: "BUY",
  orderType: "LMT",
  totalQuantity: 25,
  limitPrice: -3.65,
  auxPrice: null,
  status: "Submitted",
  filled: 0,
  remaining: 25,
  avgFillPrice: null,
  tif: "DAY",
};

const prices: Record<string, PriceData> = {
  MSFT: priceData({
    symbol: "MSFT",
    last: 355.54,
    bid: 355.5,
    ask: 355.6,
  }),
  [`MSFT_${EXPIRY}_350_P`]: priceData({
    symbol: `MSFT_${EXPIRY}_350_P`,
    bid: 6.6,
    ask: 6.9,
    last: 6.75,
    impliedVol: 0.28,
    undPrice: 355.54,
  }),
  [`MSFT_${EXPIRY}_375_C`]: priceData({
    symbol: `MSFT_${EXPIRY}_375_C`,
    bid: 3.05,
    ask: 3.35,
    last: 3.2,
    impliedVol: 0.28,
    undPrice: 355.54,
  }),
};

const portfolio = {
  positions: [],
  account_summary: {
    net_liquidation: 100_000,
    daily_pnl: 0,
    unrealized_pnl: 0,
    realized_pnl: 0,
    settled_cash: 100_000,
    maintenance_margin: 0,
    excess_liquidity: 100_000,
    buying_power: 200_000,
    available_funds: 100_000,
    dividends: 0,
  },
} as unknown as PortfolioData;

describe("ModifyOrderModal signed risk reversal prices", () => {
  it("sell combo review models submitted direction", () => {
    expect(effectiveComboLegAction("SELL", "SELL")).toBe("BUY");
    expect(effectiveComboLegAction("BUY", "SELL")).toBe("SELL");
    expect(effectiveComboLegAction("SELL", "BUY")).toBe("SELL");
  });

  it("shows signed negative combo references and submits a negative replacement limit", () => {
    const onConfirm = vi.fn();

    render(
      <ModifyOrderModal
        order={riskReversalOrder}
        loading={false}
        prices={prices}
        portfolio={portfolio}
        onConfirm={onConfirm}
        onClose={() => {}}
      />,
    );

    const modal = document.querySelector(".modify-dialog");
    expect(modal).not.toBeNull();
    const scope = within(modal as HTMLElement);

    expect(scope.getByRole("button", { name: /BID -3\.65/i })).toBeTruthy();
    expect(scope.getByRole("button", { name: /MID -3\.45/i })).toBeTruthy();
    expect(scope.getByRole("button", { name: /ASK -3\.25/i })).toBeTruthy();
    expect(scope.getByRole("button", { name: /IMPLIED -/i })).toBeTruthy();

    const input = scope.getByLabelText(/New Net Price/i) as HTMLInputElement;
    expect(input.min).toBe("");
    fireEvent.change(input, { target: { value: "-3.40" } });

    const modify = screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;
    expect(modify.disabled).toBe(false);
    fireEvent.click(modify);

    expect(onConfirm).toHaveBeenCalledWith({
      replaceOrder: {
        type: "combo",
        symbol: "MSFT",
        action: "BUY",
        quantity: 25,
        limitPrice: -3.4,
        tif: "DAY",
        legs: [
          { action: "SELL", right: "P", strike: 350, expiry: EXPIRY, ratio: 1 },
          { action: "BUY", right: "C", strike: 375, expiry: EXPIRY, ratio: 1 },
        ],
      },
    });
  });
});
