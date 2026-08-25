/**
 * @vitest-environment jsdom
 *
 * T-085 — the suppressed-submit contract (REL-027 / R-051) asserted
 * BEHAVIORALLY on every order-entry surface, not by counting
 * `placeOrderFeedback(` in source (that count survives a call whose result
 * is discarded next to a hardcoded success toast).
 *
 * Per surface: render, drive a real submit against a fetch stub whose
 * POST /api/orders/place resolves `{ deduplicated: true, orderId: 42 }`, and
 * assert the operator sees a warning-toned "NOT sent again" state — never a
 * plain success. `order-dedup-visibility.test.tsx` keeps the pure
 * `placeOrderFeedback` unit tests and the MobileOrderTicket render.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import type { LinearOrderRiskInput } from "@/lib/order/risk/useOrderRisk";

const notifications = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("@/lib/OrderActionsContext", () => ({
  useOrderActions: () => ({
    pendingCancels: new Map(),
    pendingModifies: new Map(),
    cancelledOrders: [],
    requestCancel: vi.fn(),
    requestModify: vi.fn(),
    pushNotification: notifications.push,
    drainNotifications: vi.fn(() => []),
    setOrdersUpdater: vi.fn(),
  }),
  useOrderActionsOptional: () => ({ pushNotification: notifications.push }),
}));
vi.mock("@/components/ModifyOrderModal", () => ({ default: () => null }));
vi.mock("@/components/ComboSkewPanel", () => ({ default: () => null }));
// OptionsChainTab deep-links its filters via next/navigation.
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/test",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), prefetch: vi.fn(), back: vi.fn(), forward: vi.fn(), refresh: vi.fn() }),
}));

import OrderTab from "../components/ticker-detail/OrderTab";
import SingleLegOrderTicket from "../components/SingleLegOrderTicket";
import OptionsChainTab from "../components/ticker-detail/OptionsChainTab";
import PositionTradeTicket from "../components/ticker-detail/PositionTradeTicket";
import { ListedContractOrderForm } from "../components/ticker-detail/ListedContractOrderForm";
import ChatPanel from "../components/ChatPanel";
import { TickerDetailProvider } from "../lib/TickerDetailContext";
import { placeProposedOrder } from "../lib/chat";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  notifications.push.mockReset();
});

// ── Shared stubs ────────────────────────────────────────────────────────

const DEDUP_BODY = { status: "ok", orderId: 42, deduplicated: true };
const SUPPRESSED = /NOT sent again/;

function stubFetch(routes: Record<string, unknown> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url =
      typeof input === "string" ? input : input instanceof URL ? input.toString() : (input as Request).url;
    const key = Object.keys(routes).find((k) => url.includes(k));
    const body = key ? routes[key] : url.includes("/api/orders/place") ? DEDUP_BODY : {};
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function placeCalls(fetchMock: ReturnType<typeof stubFetch>) {
  return fetchMock.mock.calls.filter(([input]) => String(input).includes("/api/orders/place"));
}

async function expectSuppressedNotification(fetchMock: ReturnType<typeof stubFetch>) {
  await waitFor(() => expect(notifications.push).toHaveBeenCalled());
  expect(placeCalls(fetchMock)).toHaveLength(1);
  expect(notifications.push).toHaveBeenCalledTimes(1);
  const [notification] = notifications.push.mock.calls[0] as [{ type: string; message: string }];
  expect(notification.type).toBe("warning");
  expect(notification.message).toMatch(SUPPRESSED);
  expect(notification.message).toContain("#42");
  expect(notification.message).not.toMatch(/^Order placed|^Combo order placed/);
}

async function clickWhenEnabled(name: RegExp | string) {
  const button = await screen.findByRole("button", { name });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(button);
  return button;
}

function makePrice(symbol: string, bid: number, ask: number): PriceData {
  return {
    symbol,
    last: (bid + ask) / 2,
    lastIsCalculated: false,
    bid,
    ask,
    bidSize: 1,
    askSize: 1,
    volume: 100,
    high: null,
    low: null,
    open: null,
    close: (bid + ask) / 2,
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
  };
}

const EMPTY_PORTFOLIO = { positions: [] } as unknown as PortfolioData;

function portfolioWith(position: PortfolioPosition): PortfolioData {
  return {
    bankroll: 250_000,
    peak_value: 250_000,
    last_sync: new Date().toISOString(),
    total_deployed_pct: 4,
    total_deployed_dollars: 9_750,
    remaining_capacity_pct: 96,
    position_count: 1,
    defined_risk_count: 1,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
    positions: [position],
    account_summary: {
      net_liquidation: 250_000,
      daily_pnl: 0,
      unrealized_pnl: 0,
      realized_pnl: 0,
      settled_cash: 240_250,
      maintenance_margin: 0,
      excess_liquidity: 240_250,
      buying_power: 480_500,
      dividends: 0,
    },
  } as unknown as PortfolioData;
}

// ── OrderTab: single-leg + combo ────────────────────────────────────────

const LONG_USAX_CALL: PortfolioPosition = {
  id: 11,
  ticker: "USAX",
  structure: "Long Call $45.0",
  structure_type: "Long Call",
  risk_profile: "defined",
  expiry: "2027-01-15",
  contracts: 65,
  direction: "LONG",
  entry_cost: 9750,
  max_risk: 9750,
  market_value: 26000,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-03-01",
  legs: [
    {
      direction: "LONG",
      contracts: 65,
      type: "Call",
      strike: 45,
      entry_cost: 9750,
      avg_cost: 150,
      market_price: 4.0,
      market_value: 26000,
      market_price_is_calculated: false,
    },
  ],
};

const USAX_PRICES: Record<string, PriceData> = {
  USAX_20270115_45_C: makePrice("USAX_20270115_45_C", 3.8, 4.1),
  USAX: makePrice("USAX", 50, 50.2),
};

const IWM_RISK_REVERSAL: PortfolioPosition = {
  id: 12,
  ticker: "IWM",
  structure: "Risk Reversal (P$243.0/C$247.0)",
  structure_type: "Risk Reversal",
  risk_profile: "undefined",
  expiry: "2026-03-26",
  contracts: 50,
  direction: "COMBO",
  entry_cost: -579.79,
  max_risk: null,
  market_value: 750,
  kelly_optimal: null,
  target: null,
  stop: null,
  entry_date: "2026-03-19",
  legs: [
    {
      direction: "LONG",
      contracts: 50,
      type: "Call",
      strike: 247,
      entry_cost: 17285.02,
      avg_cost: 346,
      market_price: 3.63,
      market_value: 18150,
      market_price_is_calculated: false,
    },
    {
      direction: "SHORT",
      contracts: 50,
      type: "Put",
      strike: 243,
      entry_cost: 17864.81,
      avg_cost: 357,
      market_price: 3.88,
      market_value: 19400,
      market_price_is_calculated: false,
    },
  ],
};

const IWM_PRICES: Record<string, PriceData> = {
  IWM_20260326_247_C: makePrice("IWM_20260326_247_C", 3.4, 3.46),
  IWM_20260326_243_P: makePrice("IWM_20260326_243_P", 3.8, 3.86),
};

describe("OrderTab single-leg — a suppressed duplicate is a warning, not a placement", () => {
  it("pushes a warning-toned NOT-sent-again notification on deduplicated:true", async () => {
    const fetchMock = stubFetch();
    const { container } = render(
      <OrderTab ticker="USAX" position={LONG_USAX_CALL} portfolio={portfolioWith(LONG_USAX_CALL)} prices={USAX_PRICES} openOrders={[]} />,
    );
    fireEvent.change(container.querySelector<HTMLInputElement>(".order-input")!, { target: { value: "65" } });
    fireEvent.change(container.querySelector<HTMLInputElement>(".modify-price-input")!, { target: { value: "4.00" } });
    await clickWhenEnabled("Place Order");
    await clickWhenEnabled("Confirm Order");

    await expectSuppressedNotification(fetchMock);
  });
});

describe("OrderTab combo — a suppressed duplicate is a warning, not a placement", () => {
  it("pushes a warning-toned NOT-sent-again notification on deduplicated:true", async () => {
    const fetchMock = stubFetch();
    render(
      <OrderTab ticker="IWM" position={IWM_RISK_REVERSAL} portfolio={portfolioWith(IWM_RISK_REVERSAL)} prices={IWM_PRICES} openOrders={[]} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /MID -0.40/i }));
    await clickWhenEnabled("Place Combo Order");
    await clickWhenEnabled("Confirm Order");

    await expectSuppressedNotification(fetchMock);
  });
});

// ── SingleLegOrderTicket ────────────────────────────────────────────────

describe("SingleLegOrderTicket — a suppressed duplicate is a warning, not a placement", () => {
  it("routes the warning to the toast sink AND the inline dedup block", async () => {
    const fetchMock = stubFetch();
    const onSuccessToast = vi.fn();
    const { container } = render(
      <SingleLegOrderTicket
        defaultAction="SELL"
        defaultTif="DAY"
        quantity="100"
        onQuantityChange={() => {}}
        quantityPlaceholder="Shares"
        bid={170}
        mid={171}
        ask={172}
        isValid
        limitPrice="171.00"
        onLimitPriceChange={() => {}}
        riskInput={{
          type: "linear",
          ticker: "AAPL",
          instrument: "stock",
          action: "SELL",
          quantity: 100,
          limitPrice: 171,
          multiplier: 1,
          heldQuantity: 100,
          closeOut: { entryCostDollars: 17_000 },
          description: "SELL 100 AAPL",
        }}
        portfolio={EMPTY_PORTFOLIO}
        riskSurface="dedup-surfaces-test"
        buildPayload={({ action, quantity, limitPrice, tif }) => ({ type: "stock", symbol: "AAPL", action, quantity, limitPrice, tif })}
        buildSuccessMessage={() => "Order placed: SELL 100 AAPL"}
        onSuccessToast={onSuccessToast}
      />,
    );
    await clickWhenEnabled("Place Order");
    await clickWhenEnabled("Confirm Order");

    await waitFor(() => expect(onSuccessToast).toHaveBeenCalled());
    expect(placeCalls(fetchMock)).toHaveLength(1);
    expect(onSuccessToast).toHaveBeenCalledTimes(1);
    const [message, tone] = onSuccessToast.mock.calls[0] as [string, string];
    expect(tone).toBe("warning");
    expect(message).toMatch(SUPPRESSED);
    expect(message).toContain("#42");
    const inline = container.querySelector(".order-success");
    expect(inline?.className).toMatch(/order-success--dedup/);
    expect(inline?.textContent).toMatch(SUPPRESSED);
  });
});

// ── OptionsChainTab (chain order builder) ───────────────────────────────

const CHAIN_TICKER = "PLTR";
const CHAIN_EXPIRY = "20991231";
const CHAIN_STRIKES = [148, 150, 152.5];
const CHAIN_SPOT = 153.1;
const CHAIN_PRICES: Record<string, PriceData> = {
  [CHAIN_TICKER]: makePrice(CHAIN_TICKER, CHAIN_SPOT - 0.05, CHAIN_SPOT + 0.05),
  [`${CHAIN_TICKER}_${CHAIN_EXPIRY}_150_C`]: makePrice(`${CHAIN_TICKER}_${CHAIN_EXPIRY}_150_C`, 5.0, 5.4),
};

function chainRoutes() {
  return {
    "/api/options/expirations": { symbol: CHAIN_TICKER, expirations: [CHAIN_EXPIRY] },
    "/api/options/chain": { symbol: CHAIN_TICKER, expiry: CHAIN_EXPIRY, strikes: CHAIN_STRIKES },
    "/api/risk-free-rate": { rate: 0 },
    "/api/previous-close": { closes: { [CHAIN_TICKER]: CHAIN_SPOT } },
  };
}

describe("OptionsChainTab order builder — a suppressed duplicate is a warning, not a placement", () => {
  it("pushes a warning-toned NOT-sent-again notification on deduplicated:true", async () => {
    // jsdom has no Element.scrollTo; the chain's ATM-centering effect calls it.
    Object.defineProperty(Element.prototype, "scrollTo", { configurable: true, writable: true, value: vi.fn() });
    const fetchMock = stubFetch(chainRoutes());
    const { container } = render(
      <TickerDetailProvider>
        <OptionsChainTab
          ticker={CHAIN_TICKER}
          prices={CHAIN_PRICES}
          tickerPriceData={CHAIN_PRICES[CHAIN_TICKER]}
          portfolio={EMPTY_PORTFOLIO}
        />
      </TickerDetailProvider>,
    );
    await waitFor(() => expect(container.querySelectorAll(".chain-strike").length).toBeGreaterThan(0));
    const row150 = Array.from(container.querySelectorAll("tr")).find((row) =>
      /150\.00/.test(row.querySelector(".chain-strike")?.textContent ?? ""),
    );
    expect(row150).toBeTruthy();
    fireEvent.click(row150!.querySelector(".chain-mid.chain-clickable")!);

    await clickWhenEnabled(/^Place /);
    await clickWhenEnabled("Confirm Order");

    await expectSuppressedNotification(fetchMock);
  });
});

// ── PositionTradeTicket ─────────────────────────────────────────────────

const MU_RISK_REVERSAL = {
  id: 7,
  ticker: "MU",
  structure: "Risk Reversal (P$800.0/C$1050.0)",
  structure_type: "Risk Reversal",
  direction: "COMBO",
  contracts: 5,
  expiry: "2026-07-17",
  entry_date: "2026-05-29",
  entry_cost: -3495,
  market_value: -46290,
  legs: [
    { direction: "SHORT", type: "Call", strike: 1050, contracts: 3, avg_cost: 10999, entry_cost: -32997, market_price: 133.93, market_price_is_calculated: false },
    { direction: "LONG", type: "Put", strike: 800, contracts: 5, avg_cost: 5900, entry_cost: 29500, market_price: 41.0, market_price_is_calculated: false },
  ],
} as unknown as PortfolioPosition;

const MU_PRICES: Record<string, PriceData> = {
  MU_20260717_1050_C: makePrice("MU_20260717_1050_C", 130, 134),
  MU_20260717_800_P: makePrice("MU_20260717_800_P", 40, 42),
};

describe("PositionTradeTicket — a suppressed duplicate is a warning, not a placement", () => {
  it("pushes a warning-toned NOT-sent-again notification on deduplicated:true", async () => {
    const fetchMock = stubFetch();
    const onClose = vi.fn();
    render(
      <PositionTradeTicket
        position={MU_RISK_REVERSAL}
        prices={MU_PRICES}
        portfolio={portfolioWith(MU_RISK_REVERSAL)}
        target={{ kind: "leg", index: 1 }}
        onClose={onClose}
      />,
    );
    fireEvent.change(screen.getByTestId("position-trade-limit"), { target: { value: "41.00" } });
    await clickWhenEnabled(/review order/i);
    await clickWhenEnabled(/confirm order/i);

    await expectSuppressedNotification(fetchMock);
    expect(onClose).toHaveBeenCalled();
  });
});

// ── ListedContractOrderForm (futures + index options shared form) ───────

function listedRiskInput({ action, quantity, limitPrice }: { action: "BUY" | "SELL"; quantity: string; limitPrice: string }): LinearOrderRiskInput | null {
  const price = parseFloat(limitPrice);
  const qty = parseInt(quantity, 10);
  if (!Number.isFinite(price) || price <= 0 || !Number.isFinite(qty) || qty <= 0) return null;
  return {
    type: "linear",
    ticker: "VIX",
    instrument: "future",
    action,
    quantity: qty,
    limitPrice: price,
    multiplier: 1000,
    heldQuantity: 0,
    heldShortQuantity: 0,
    closeOut: null,
    description: `${action} ${qty} VIXU6 @ $${price.toFixed(2)}`,
  };
}

describe("ListedContractOrderForm — a suppressed duplicate is a warning, not a placement", () => {
  it("renders the dedup success block with NOT-sent-again copy on deduplicated:true", async () => {
    const fetchMock = stubFetch();
    const { container } = render(
      <ListedContractOrderForm
        eyebrow="VIX"
        contractSelector={null}
        multiplier={1000}
        multiplierDisplay="1,000"
        notionalLabel="Notional"
        limitPriceLabel="Limit"
        limitPriceStep={0.05}
        buildRiskInput={listedRiskInput}
        portfolio={EMPTY_PORTFOLIO}
        surface="dedup-surfaces-test"
        buildSubmit={() => ({ payload: { type: "future", symbol: "VIX" }, successText: "Order placed: BUY 1 VIXU6" })}
        submitLabel="BUY VIXU6"
        submitDisabled={false}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText("0.00"), { target: { value: "20" } });
    await clickWhenEnabled("BUY VIXU6");

    await waitFor(() => expect(container.querySelector(".futures-form-success")).toBeTruthy());
    expect(placeCalls(fetchMock)).toHaveLength(1);
    const success = container.querySelector(".futures-form-success")!;
    expect(success.className).toMatch(/futures-form-success--dedup/);
    expect(success.textContent).toMatch(SUPPRESSED);
    expect(success.textContent).toContain("#42");
    expect(success.textContent).not.toMatch(/^Order placed/);
  });
});

// ── Chat: placeProposedOrder + ChatPanel confirm card ───────────────────

const PROPOSAL = {
  tool: "place_order",
  destructive: true as const,
  input: { type: "option" as const, ticker: "WULF", action: "BUY" as const, quantity: 10, limit_price: 5.6, expiry: "20260918", strike: 6, right: "C" as const, conId: 12345, exchange: "SMART" },
  summary: "BUY 10 WULF long call @ 5.6",
  toolUseId: "tu-1",
};

describe("lib/chat placeProposedOrder — a suppressed duplicate is reported as NOT sent again", () => {
  it("returns the suppressed-submit message instead of the placed message", async () => {
    stubFetch();
    const result = await placeProposedOrder(PROPOSAL);
    expect(result.ok).toBe(true);
    expect(result.message).toMatch(SUPPRESSED);
    expect(result.message).toContain("#42");
    expect(result.message).not.toMatch(/^Order placed/);
  });
});

describe("ChatPanel confirm card — a suppressed duplicate is visibly NOT a placement", () => {
  it("renders the NOT-sent-again message, not 'Order placed', on deduplicated:true", async () => {
    stubFetch({ "/api/assistant": { content: "Proposing an order.", proposal: PROPOSAL } });
    render(<ChatPanel activeSection="dashboard" portfolio={EMPTY_PORTFOLIO} />);
    const textarea = screen.getByLabelText("Ask Radon");
    fireEvent.change(textarea, { target: { value: "buy me some wulf calls" } });
    fireEvent.submit(textarea.closest("form")!);
    await clickWhenEnabled(/confirm/i);

    await screen.findByText(SUPPRESSED);
    expect(screen.queryByText(`Order placed: ${PROPOSAL.summary}`)).toBeNull();
  });
});
