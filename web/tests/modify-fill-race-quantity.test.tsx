/**
 * @vitest-environment jsdom
 *
 * REL-232 / R-633: a fill landing while the modify dialog is open must not
 * silently ENLARGE the order.
 *
 * The quantity field is seeded once per permId with the remainder, but
 * `quantityChanged` and the transmitted total were computed against the LIVE
 * polled order prop. BUY 1000 filled 16: dialog opens at 984; a 100-lot fills
 * mid-edit; a price-only submit sees 884 != 984, flips quantityChanged, and
 * transmits newQuantity = 116 + 984 = 1100. The combo-replace branch has the
 * same race and over-orders the stale remainder.
 *
 * These tests render the modal wired to the REAL fetch owner
 * (OrderActionsContext.requestModify) with a stubbed global fetch, advance
 * `filled` between open and submit, and assert at the wire.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { OpenOrder, PortfolioData } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import ModifyOrderModal from "@/components/ModifyOrderModal";
import { OrderActionsProvider, useOrderActions } from "@/lib/OrderActionsContext";

vi.mock("@/lib/useRiskFreeRate", () => ({
  useRiskFreeRate: () => 0,
}));

vi.mock("@/components/Modal", () => ({
  default: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? React.createElement("div", { className: "mock-modal" }, children) : null,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

type RecordedCall = { url: string; method: string; body: unknown };

function recordFetch(): RecordedCall[] {
  const calls: RecordedCall[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
      calls.push({ url, method: init?.method ?? "GET", body });
      return new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

/** The order from R-633: VIX C30, 1000 requested, 16 filled at open. */
function vixOrder(overrides: Partial<OpenOrder> = {}): OpenOrder {
  return {
    orderId: 7,
    permId: 7007,
    symbol: "VIX",
    contract: {
      conId: 71,
      symbol: "VIX",
      secType: "OPT",
      strike: 30,
      right: "C",
      expiry: "20261020",
    },
    action: "BUY",
    orderType: "LMT",
    totalQuantity: 1000,
    limitPrice: 0.61,
    auxPrice: null,
    status: "Submitted",
    filled: 16,
    remaining: 984,
    avgFillPrice: 0.61,
    tif: "DAY",
    ...overrides,
  };
}

const PORTFOLIO = { positions: [], bankroll: 1_000_000 } as unknown as PortfolioData;

/** Renders the modal wired to the real fetch owner, requestModify. */
function Harness({ order, prices }: { order: OpenOrder; prices?: Record<string, PriceData> }) {
  const { requestModify } = useOrderActions();
  return (
    <ModifyOrderModal
      order={order}
      loading={false}
      prices={prices}
      portfolio={PORTFOLIO}
      onConfirm={(request) => {
        void requestModify(order, request);
      }}
      onClose={() => {}}
    />
  );
}

function renderHarness(order: OpenOrder, prices?: Record<string, PriceData>) {
  const calls = recordFetch();
  const view = render(
    <OrderActionsProvider>
      <Harness order={order} prices={prices} />
    </OrderActionsProvider>,
  );
  const rerenderWith = (next: OpenOrder) =>
    view.rerender(
      <OrderActionsProvider>
        <Harness order={next} prices={prices} />
      </OrderActionsProvider>,
    );
  return { calls, rerenderWith };
}

const qtyInput = () =>
  document.getElementById("modify-quantity-input") as HTMLInputElement;
const priceInput = () =>
  document.getElementById("modify-price-input") as HTMLInputElement;
const submitBtn = () =>
  screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;

describe("price-only edit while a fill lands mid-dialog", () => {
  it("transmits the new price WITHOUT any newQuantity", async () => {
    const { calls, rerenderWith } = renderHarness(vixOrder());
    expect(qtyInput().value).toBe("984");

    fireEvent.change(priceInput(), { target: { value: "0.70" } });
    // A 100-lot fills while the dialog is open: filled 16 -> 116.
    rerenderWith(vixOrder({ filled: 116, remaining: 884 }));

    await act(async () => {
      fireEvent.click(submitBtn());
    });

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/orders/modify");
    expect(calls[0].method).toBe("POST");
    // The buggy live-prop math sent newQuantity = 116 + 984 = 1100 here,
    // GROWING the order on an edit the operator never made.
    expect("newQuantity" in (calls[0].body as object)).toBe(false);
    expect(calls[0].body).toEqual({ orderId: 7, permId: 7007, newPrice: 0.7 });
  });
});

describe("edited quantity while a fill lands mid-dialog", () => {
  it("refuses to transmit snapshot+stale math and re-prompts", async () => {
    const { calls, rerenderWith } = renderHarness(vixOrder());

    fireEvent.change(qtyInput(), { target: { value: "500" } });
    rerenderWith(vixOrder({ filled: 116, remaining: 884 }));

    await act(async () => {
      fireEvent.click(submitBtn());
    });

    // Nothing on the wire: the fill count the total was computed from is stale.
    expect(calls).toHaveLength(0);
    // Re-prompt: field reseeded to the CURRENT remainder, operator warned.
    expect(qtyInput().value).toBe("884");
    expect(document.querySelector(".modify-fill-race")?.textContent).toMatch(/fill/i);
  });

  it("after the re-prompt, a resubmit uses the refreshed fill count", async () => {
    const { calls, rerenderWith } = renderHarness(vixOrder());

    fireEvent.change(qtyInput(), { target: { value: "500" } });
    const live = vixOrder({ filled: 116, remaining: 884 });
    rerenderWith(live);

    await act(async () => {
      fireEvent.click(submitBtn());
    });
    expect(calls).toHaveLength(0);

    fireEvent.change(qtyInput(), { target: { value: "500" } });
    await act(async () => {
      fireEvent.click(submitBtn());
    });

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/orders/modify");
    expect(calls[0].method).toBe("POST");
    // 116 now filled + 500 still wanted = 616 total.
    expect(calls[0].body).toEqual({ orderId: 7, permId: 7007, newQuantity: 616 });
  });
});

describe("combo replace while a fill lands mid-dialog", () => {
  // Window-relative expiry: a hardcoded date rots to T~0 in CI (tasks/lessons.md).
  const EXPIRY = (() => {
    const d = new Date();
    d.setDate(d.getDate() + 180);
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${d.getFullYear()}${mm}${dd}`;
  })();
  const EXPIRY_DASHED = `${EXPIRY.slice(0, 4)}-${EXPIRY.slice(4, 6)}-${EXPIRY.slice(6)}`;

  function pd(overrides: Partial<PriceData> & { symbol: string }): PriceData {
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
      // Fresh timestamp: the REL-236 quote gate disarms submit on stale
      // quotes, and this suite exercises the fill race, not staleness.
      timestamp: new Date().toISOString(),
      ...overrides,
    };
  }

  function comboOrder(overrides: Partial<OpenOrder> = {}): OpenOrder {
    return {
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
      filled: 5,
      remaining: 20,
      avgFillPrice: -3.65,
      tif: "DAY",
      ...overrides,
    };
  }

  const prices: Record<string, PriceData> = {
    MSFT: pd({ symbol: "MSFT", last: 355.54, bid: 355.5, ask: 355.6 }),
    [`MSFT_${EXPIRY}_350_P`]: pd({
      symbol: `MSFT_${EXPIRY}_350_P`,
      bid: 6.6,
      ask: 6.9,
      last: 6.75,
      impliedVol: 0.28,
      undPrice: 355.54,
    }),
    [`MSFT_${EXPIRY}_375_C`]: pd({
      symbol: `MSFT_${EXPIRY}_375_C`,
      bid: 3.05,
      ask: 3.35,
      last: 3.2,
      impliedVol: 0.28,
      undPrice: 355.54,
    }),
  };

  it("refuses the replacement when live filled moved past the snapshot", async () => {
    const { calls, rerenderWith } = renderHarness(comboOrder(), prices);
    expect(qtyInput().value).toBe("20");

    fireEvent.change(priceInput(), { target: { value: "-3.40" } });
    // Two more units fill while the operator edits: filled 5 -> 7.
    rerenderWith(comboOrder({ filled: 7, remaining: 18 }));

    expect(submitBtn().disabled).toBe(false);
    await act(async () => {
      fireEvent.click(submitBtn());
    });

    // The buggy branch cancel-and-replaced at the STALE remainder (20) and
    // over-ordered by the 2 that had just filled. No modify may hit the wire
    // (the risk gate's read-only /api/orders/whatif probe is not a mutation).
    const mutations = calls.filter((call) => call.url === "/api/orders/modify");
    expect(mutations).toHaveLength(0);
    // Everything that did fire is a read-only risk-gate probe, not an order.
    expect(calls.map((call) => call.url)).toEqual(
      calls.map((call) => call.url).filter((url) =>
        url === "/api/orders/whatif" || url.startsWith("/api/short-availability/"),
      ),
    );
    expect(qtyInput().value).toBe("18");
    expect(document.querySelector(".modify-fill-race")?.textContent).toMatch(/fill/i);
  });
});
