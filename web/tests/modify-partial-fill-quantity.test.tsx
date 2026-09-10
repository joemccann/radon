/**
 * @vitest-environment jsdom
 *
 * Modifying a PARTIALLY FILLED order must work in REMAINING quantity.
 *
 * A 1000-lot that has filled 16 has 984 still working. The dialog used to
 * seed the field with `totalQuantity` (1000) and price the ticket as if
 * 1000 contracts were still to be bought — $61,000 of cost and margin
 * against a remainder that only costs $60,024.
 *
 * The wire is the other half: IB's modify assigns `trade.order.totalQuantity`
 * (scripts/ib_order_manage.py), so an edited REMAINING figure must be sent
 * back as `filled + entered`. Sending the remainder raw would silently shrink
 * the order by everything already filled.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { OpenOrder, PortfolioData } from "@/lib/types";
import ModifyOrderModal from "@/components/ModifyOrderModal";
import { OrderActionsProvider, useOrderActions } from "@/lib/OrderActionsContext";
import {
  filledQuantity,
  isPartiallyFilled,
  remainingQuantity,
  toIbTotalQuantity,
} from "@/lib/orders/modifyQuantity";

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

/** The order from the report: VIX C30, 1000 requested, 16 filled. */
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

function renderModal(order: OpenOrder, onConfirm = vi.fn()) {
  const view = render(
    <ModifyOrderModal
      order={order}
      loading={false}
      portfolio={PORTFOLIO}
      onConfirm={onConfirm}
      onClose={() => {}}
    />,
  );
  return { ...view, onConfirm };
}

const qtyInput = () =>
  document.getElementById("modify-quantity-input") as HTMLInputElement;
const submitBtn = () =>
  screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;

describe("modifyQuantity helpers", () => {
  it("reports the remainder of a partially filled order", () => {
    const order = vixOrder();
    expect(filledQuantity(order)).toBe(16);
    expect(remainingQuantity(order)).toBe(984);
    expect(isPartiallyFilled(order)).toBe(true);
  });

  it("treats an unfilled order as wholly remaining", () => {
    const order = vixOrder({ filled: 0, remaining: 1000 });
    expect(filledQuantity(order)).toBe(0);
    expect(remainingQuantity(order)).toBe(1000);
    expect(isPartiallyFilled(order)).toBe(false);
  });

  it("converts an edited remainder back to IB's new TOTAL", () => {
    const order = vixOrder();
    expect(toIbTotalQuantity(order, 984)).toBe(1000);
    expect(toIbTotalQuantity(order, 500)).toBe(516);
    // No partial fill: remaining IS the total, so the wire value is unchanged.
    expect(toIbTotalQuantity(vixOrder({ filled: 0 }), 400)).toBe(400);
  });

  it("ignores a nonsensical filled count rather than inventing a remainder", () => {
    for (const filled of [-5, Number.NaN, Number.POSITIVE_INFINITY, 1000, 1500]) {
      const order = vixOrder({ filled: filled as number });
      expect(filledQuantity(order)).toBe(0);
      expect(remainingQuantity(order)).toBe(1000);
    }
  });
});

describe("ModifyOrderModal prefills the REMAINING quantity", () => {
  it("seeds 984, not the original 1000", () => {
    renderModal(vixOrder());
    expect(qtyInput().value).toBe("984");
  });

  it("still seeds the full size when nothing has filled", () => {
    renderModal(vixOrder({ filled: 0, remaining: 1000 }));
    expect(qtyInput().value).toBe("1000");
  });

  it("shows the operator what already filled", () => {
    const { container } = renderModal(vixOrder());
    const info = container.querySelector(".modify-order-info");
    expect(info?.textContent).toContain("984x");
    expect(info?.textContent).toContain("16");
  });
});

describe("ModifyOrderModal partial-fill wire contract", () => {
  it("submits nothing while the remainder is untouched", () => {
    const { onConfirm } = renderModal(vixOrder());
    expect(qtyInput().value).toBe("984");
    expect(submitBtn().disabled).toBe(true);
    fireEvent.click(submitBtn());
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("sends filled + entered as the new TOTAL", () => {
    const { onConfirm } = renderModal(vixOrder());

    fireEvent.change(qtyInput(), { target: { value: "500" } });
    expect(submitBtn().disabled).toBe(false);
    fireEvent.click(submitBtn());

    expect(onConfirm).toHaveBeenCalledTimes(1);
    // 16 already filled + 500 still wanted = 516 total, NOT 500.
    expect(onConfirm.mock.calls[0][0]).toEqual({ newQuantity: 516 });
  });

  it("sends the entered value unchanged when nothing has filled", () => {
    const { onConfirm } = renderModal(vixOrder({ filled: 0, remaining: 1000 }));

    fireEvent.change(qtyInput(), { target: { value: "400" } });
    fireEvent.click(submitBtn());

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0]).toEqual({ newQuantity: 400 });
  });
});

/**
 * T-472: the wire path with fills advancing MID-DIALOG. `toIbTotalQuantity`
 * is only a `fillSnapshot == null` fallback the modal can never reach; the
 * transmitted total must come from the SNAPSHOT, and a stale-snapshot
 * quantity submit must hit the fillRaceNotice branch, never the wire.
 * Wired to the REAL fetch owner (OrderActionsContext.requestModify).
 */
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

function WiredHarness({ order }: { order: OpenOrder }) {
  const { requestModify } = useOrderActions();
  return (
    <ModifyOrderModal
      order={order}
      loading={false}
      portfolio={PORTFOLIO}
      onConfirm={(request) => {
        void requestModify(order, request);
      }}
      onClose={() => {}}
    />
  );
}

function renderWired(order: OpenOrder) {
  const calls = recordFetch();
  const view = render(
    <OrderActionsProvider>
      <WiredHarness order={order} />
    </OrderActionsProvider>,
  );
  const rerenderWith = (next: OpenOrder) =>
    view.rerender(
      <OrderActionsProvider>
        <WiredHarness order={next} />
      </OrderActionsProvider>,
    );
  return { calls, rerenderWith, container: view.container };
}

const priceInput = () =>
  document.getElementById("modify-price-input") as HTMLInputElement;

describe("T-472: fills advance 16 -> 100 while the dialog is open", () => {
  it("price-only submit transmits NO newQuantity", async () => {
    const { calls, rerenderWith } = renderWired(vixOrder());
    expect(qtyInput().value).toBe("984");

    fireEvent.change(priceInput(), { target: { value: "0.70" } });
    rerenderWith(vixOrder({ filled: 100, remaining: 900 }));

    await act(async () => {
      fireEvent.click(submitBtn());
    });

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/orders/modify");
    expect(calls[0].method).toBe("POST");
    expect("newQuantity" in (calls[0].body as object)).toBe(false);
    expect(calls[0].body).toEqual({ orderId: 7, permId: 7007, newPrice: 0.7 });
  });

  it("quantity submit hits the fillRaceNotice reseed branch, not toIbTotalQuantity", async () => {
    const { calls, rerenderWith } = renderWired(vixOrder());

    fireEvent.change(qtyInput(), { target: { value: "500" } });
    rerenderWith(vixOrder({ filled: 100, remaining: 900 }));

    await act(async () => {
      fireEvent.click(submitBtn());
    });

    // toIbTotalQuantity(live order, 500) would have sent newQuantity: 600 and
    // the snapshot arithmetic 516 — neither may reach the wire.
    expect(calls).toHaveLength(0);
    // Refuse + reseed: field now shows the CURRENT remainder, operator warned.
    expect(qtyInput().value).toBe("900");
    expect(document.querySelector(".modify-fill-race")?.textContent).toMatch(/fill/i);
  });
});

describe("T-473: info line and quantity field agree after fills advance", () => {
  it("both describe the SNAPSHOT fill count, with the live advance flagged", () => {
    const { rerenderWith, container } = renderWired(vixOrder());
    rerenderWith(vixOrder({ filled: 100, remaining: 900 }));

    const info = container.querySelector(".modify-order-info");
    // The field is still seeded from the snapshot remainder...
    expect(qtyInput().value).toBe("984");
    expect(info?.textContent).toContain("984x");
    // ...so the filled figure beside it must be the SAME snapshot count,
    // not the live 100 the field's math knows nothing about.
    expect(info?.querySelector(".modify-order-filled")?.textContent).toContain(
      "16 of 1000 filled",
    );
    // The live advance is not hidden: it is visibly flagged as stale.
    const stale = info?.querySelector(".modify-order-fill-stale");
    expect(stale?.textContent).toContain("100");
    expect(stale?.textContent).toMatch(/fill/i);
  });
});
