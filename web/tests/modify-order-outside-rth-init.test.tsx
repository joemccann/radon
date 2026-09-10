/**
 * @vitest-environment jsdom
 *
 * ModifyOrderModal must seed FILL OUTSIDE RTH from the resting order,
 * not always false.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { OpenOrder, PortfolioData } from "@/lib/types";
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

/** Explicit and empty: `portfolio === undefined` leaves the risk gate
 *  "pending" and Modify Order permanently disabled, so a wire assertion could
 *  never reach `submitModify`. */
const PORTFOLIO = { positions: [], bankroll: 100_000 } as unknown as PortfolioData;

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

const rthBox = () =>
  screen.getByRole("checkbox", { name: /FILL OUTSIDE RTH/i }) as HTMLInputElement;
const submitBtn = () =>
  screen.getByRole("button", { name: /Modify Order/i }) as HTMLButtonElement;

describe("ModifyOrderModal FILL OUTSIDE RTH init", () => {
  it("checks the box when the resting order has outsideRth true", () => {
    renderModal(stockOrder({ outsideRth: true }));
    const box = screen.getByRole("checkbox", { name: /FILL OUTSIDE RTH/i }) as HTMLInputElement;
    expect(box.checked).toBe(true);
  });

  it("leaves the box unchecked when outsideRth is missing", () => {
    // Was preceded by `expect(order.outsideRth).toBeUndefined()` on a literal
    // constructed one line above — a property of the fixture, not the product.
    renderModal(stockOrder());
    const box = screen.getByRole("checkbox", { name: /FILL OUTSIDE RTH/i }) as HTMLInputElement;
    expect(box.checked).toBe(false);
  });

  it("leaves the box unchecked when outsideRth is false", () => {
    renderModal(stockOrder({ outsideRth: false }));
    const box = screen.getByRole("checkbox", { name: /FILL OUTSIDE RTH/i }) as HTMLInputElement;
    expect(box.checked).toBe(false);
  });
});

describe("ModifyOrderModal sends the RTH change on the wire", () => {
  it("nothing is submitted while the form is unchanged", () => {
    const { onConfirm } = renderModal(stockOrder({ outsideRth: true }));
    expect(submitBtn().disabled).toBe(true);
    fireEvent.click(submitBtn());
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("un-ticking EXT sends exactly { outsideRth: false }", () => {
    const { onConfirm } = renderModal(stockOrder({ outsideRth: true }));
    fireEvent.click(rthBox());
    expect(rthBox().checked).toBe(false);

    expect(submitBtn().disabled).toBe(false);
    fireEvent.click(submitBtn());

    expect(onConfirm).toHaveBeenCalledTimes(1);
    // Equality, not a subset: an extra newPrice/newQuantity on a pure RTH
    // change is a second modification the operator never asked for.
    expect(onConfirm.mock.calls[0][0]).toEqual({ outsideRth: false });
  });

  it("ticking EXT on a resting RTH-only order sends { outsideRth: true }", () => {
    const { onConfirm } = renderModal(stockOrder({ outsideRth: false }));
    fireEvent.click(rthBox());
    fireEvent.click(submitBtn());

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0]).toEqual({ outsideRth: true });
  });

  it("a price-only change leaves outsideRth ABSENT from the request", () => {
    const { onConfirm } = renderModal(stockOrder({ outsideRth: true }));
    fireEvent.change(screen.getByLabelText(/New Limit Price/i), { target: { value: "51.25" } });
    fireEvent.click(submitBtn());

    expect(onConfirm).toHaveBeenCalledTimes(1);
    const request = onConfirm.mock.calls[0][0];
    expect(request).toEqual({ newPrice: 51.25 });
    // An unchanged flag echoed back re-asserts a value the operator did not
    // touch, and IB treats the field as a modification either way.
    expect("outsideRth" in request).toBe(false);
  });
});
