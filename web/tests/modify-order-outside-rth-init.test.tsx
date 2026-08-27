/**
 * @vitest-environment jsdom
 *
 * ModifyOrderModal must seed FILL OUTSIDE RTH from the resting order,
 * not always false.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { OpenOrder } from "@/lib/types";
import ModifyOrderModal from "@/components/ModifyOrderModal";

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
});

function stockOrder(overrides: Partial<OpenOrder> = {}): OpenOrder {
  return {
    orderId: 2,
    permId: 1002,
    symbol: "TQQQ",
    contract: {
      conId: 20,
      symbol: "TQQQ",
      secType: "STK",
      strike: null,
      right: null,
      expiry: null,
    },
    action: "BUY",
    orderType: "LMT",
    totalQuantity: 10,
    limitPrice: 50,
    auxPrice: null,
    status: "Submitted",
    filled: 0,
    remaining: 10,
    avgFillPrice: null,
    tif: "GTC",
    ...overrides,
  };
}

function renderModal(order: OpenOrder) {
  return render(
    <ModifyOrderModal
      order={order}
      loading={false}
      onConfirm={vi.fn()}
      onClose={() => {}}
    />,
  );
}

describe("ModifyOrderModal FILL OUTSIDE RTH init", () => {
  it("checks the box when the resting order has outsideRth true", () => {
    renderModal(stockOrder({ outsideRth: true }));
    const box = screen.getByRole("checkbox", { name: /FILL OUTSIDE RTH/i }) as HTMLInputElement;
    expect(box.checked).toBe(true);
  });

  it("leaves the box unchecked when outsideRth is missing", () => {
    const order = stockOrder();
    expect(order.outsideRth).toBeUndefined();
    renderModal(order);
    const box = screen.getByRole("checkbox", { name: /FILL OUTSIDE RTH/i }) as HTMLInputElement;
    expect(box.checked).toBe(false);
  });

  it("leaves the box unchecked when outsideRth is false", () => {
    renderModal(stockOrder({ outsideRth: false }));
    const box = screen.getByRole("checkbox", { name: /FILL OUTSIDE RTH/i }) as HTMLInputElement;
    expect(box.checked).toBe(false);
  });
});
