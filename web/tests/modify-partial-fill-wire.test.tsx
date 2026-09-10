/**
 * @vitest-environment jsdom
 *
 * T-442: partial-fill modify tested AT THE WIRE.
 *
 * modify-partial-fill-quantity.test.tsx stops at a vi.fn() onConfirm prop.
 * The real request is fetch("/api/orders/modify", ...) in
 * lib/OrderActionsContext.tsx (requestModify); OrderActionsContext applies
 * `pm.newQuantity ?? o.totalQuantity`, so a dropped newQuantity degrades
 * silently into a no-op resend at the ORIGINAL quantity. Per CLAUDE.md, a
 * gated action needs the component tree that OWNS the fetch, a stubbed
 * fetch, the FULL url + method + payload shape, and a paired assertion that
 * nothing fired while the gate was closed.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { OpenOrder, PortfolioData } from "@/lib/types";
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

/** The order from the report: VIX C30, 1000 requested, 16 filled. */
function vixOrder(): OpenOrder {
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
  };
}

const PORTFOLIO = { positions: [], bankroll: 1_000_000 } as unknown as PortfolioData;

/**
 * Mirrors the production wiring (OrderTab / WorkspaceSections): the modal's
 * onConfirm hands the request to OrderActionsContext.requestModify, which
 * owns the fetch.
 */
function ModalWithWire({ order }: { order: OpenOrder }) {
  const { requestModify } = useOrderActions();
  return (
    <ModifyOrderModal
      order={order}
      loading={false}
      portfolio={PORTFOLIO}
      onConfirm={(request) => void requestModify(order, request)}
      onClose={() => {}}
    />
  );
}

function renderWired(order: OpenOrder) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({}),
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <OrderActionsProvider>
      <ModalWithWire order={order} />
    </OrderActionsProvider>,
  );
  return fetchMock;
}

const qtyInput = () =>
  document.getElementById("modify-quantity-input") as HTMLInputElement;
const submitBtn = () =>
  screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;

describe("partial-fill modify wire contract (OrderActionsContext + ModifyOrderModal)", () => {
  it("fires ZERO requests while the gate is closed (remainder untouched)", () => {
    const fetchMock = renderWired(vixOrder());
    expect(qtyInput().value).toBe("984");
    expect(submitBtn().disabled).toBe(true);
    fireEvent.click(submitBtn());
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("POSTs /api/orders/modify with newQuantity 516 (filled 16 + entered 500)", async () => {
    const fetchMock = renderWired(vixOrder());

    fireEvent.change(qtyInput(), { target: { value: "500" } });
    expect(submitBtn().disabled).toBe(false);
    fireEvent.click(submitBtn());

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    // Full URL string, not includes() — a wrong endpoint must fail.
    expect(url).toBe("/api/orders/modify");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    const payload = JSON.parse(init.body as string);
    expect(payload).toEqual({
      orderId: 7,
      permId: 7007,
      newQuantity: 516,
    });
  });
});
