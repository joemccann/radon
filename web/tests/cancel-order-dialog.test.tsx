/**
 * @vitest-environment jsdom
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { OpenOrder } from "@/lib/types";
import CancelOrderDialog from "../components/CancelOrderDialog";

vi.mock("../components/Modal", () => ({
  default: ({
    open,
    title,
    children,
  }: {
    open: boolean;
    title: string;
    children: React.ReactNode;
  }) =>
    open
      ? React.createElement(
          "div",
          { role: "dialog", "aria-label": title, "data-testid": "cancel-dialog-modal" },
          React.createElement("h2", null, title),
          children,
        )
      : null,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeOrder(overrides: Partial<OpenOrder> = {}): OpenOrder {
  const totalQuantity = overrides.totalQuantity ?? 10;
  return {
    orderId: overrides.orderId ?? 1,
    permId: overrides.permId ?? 1001,
    symbol: overrides.symbol ?? "AAPL",
    contract: overrides.contract ?? {
      conId: 1234,
      symbol: overrides.symbol ?? "AAPL",
      secType: "OPT",
      strike: 200,
      right: "C",
      expiry: "2026-07-17",
    },
    action: overrides.action ?? "BUY",
    orderType: overrides.orderType ?? "LMT",
    totalQuantity,
    limitPrice: overrides.limitPrice !== undefined ? overrides.limitPrice : 2.5,
    auxPrice: overrides.auxPrice ?? null,
    status: overrides.status ?? "Submitted",
    filled: overrides.filled ?? 0,
    remaining: overrides.remaining ?? totalQuantity,
    avgFillPrice: overrides.avgFillPrice ?? null,
    tif: overrides.tif ?? "DAY",
  };
}

function assertNoEmDash(text: string) {
  expect(text).not.toMatch(/\u2014/);
  expect(text).not.toMatch(/—/);
}

describe("CancelOrderDialog — single order", () => {
  it("renders single order details", () => {
    const order = makeOrder({
      symbol: "NVDA",
      action: "SELL",
      orderType: "LMT",
      totalQuantity: 5,
      limitPrice: 1.25,
      status: "Submitted",
    });

    render(
      <CancelOrderDialog
        order={order}
        loading={false}
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Cancel Order" })).toBeTruthy();
    expect(screen.getByText("NVDA")).toBeTruthy();
    expect(screen.getByText("SELL")).toBeTruthy();
    expect(screen.getByText("LMT")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();
    expect(screen.getByText("$1.25")).toBeTruthy();
    expect(screen.getByText("Submitted")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Keep Order" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel Order" })).toBeTruthy();
  });

  it("shows partial-fill warning for a single partially filled order", () => {
    const order = makeOrder({
      totalQuantity: 10,
      filled: 3,
      remaining: 7,
    });

    render(
      <CancelOrderDialog
        order={order}
        loading={false}
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );

    const warning = screen.getByText(/Partially filled/);
    expect(warning.textContent).toMatch(/3 of 10/);
    expect(warning.textContent).toMatch(/remaining 7/);
  });

  it("renders nothing when order is null and no multi list", () => {
    const { container } = render(
      <CancelOrderDialog
        order={null}
        loading={false}
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );
    expect(container.textContent).toBe("");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("CancelOrderDialog — multi / combo", () => {
  it("renders multi-order cancel all CTA", () => {
    const orders = [
      makeOrder({
        orderId: 1,
        permId: 1001,
        symbol: "MSFT",
        action: "BUY",
        totalQuantity: 10,
        limitPrice: 1.5,
        contract: {
          conId: 1,
          symbol: "MSFT",
          secType: "OPT",
          strike: 400,
          right: "C",
          expiry: "2026-07-17",
        },
      }),
      makeOrder({
        orderId: 2,
        permId: 1002,
        symbol: "MSFT",
        action: "SELL",
        totalQuantity: 10,
        limitPrice: 0.8,
        contract: {
          conId: 2,
          symbol: "MSFT",
          secType: "OPT",
          strike: 390,
          right: "P",
          expiry: "2026-07-17",
        },
      }),
    ];

    render(
      <CancelOrderDialog
        order={null}
        orders={orders}
        loading={false}
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );

    expect(screen.getByRole("heading", { name: "Cancel Orders" })).toBeTruthy();
    expect(screen.getByText("MSFT")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy(); // legs count
    expect(screen.getByRole("button", { name: "Keep Orders" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel All" })).toBeTruthy();
    expect(screen.getByText(/BUY/)).toBeTruthy();
    expect(screen.getByText(/SELL/)).toBeTruthy();
  });

  it("shows partial warning for multi when one leg is partial", () => {
    const orders = [
      makeOrder({
        orderId: 1,
        permId: 1001,
        symbol: "AAPL",
        filled: 0,
        remaining: 10,
        totalQuantity: 10,
      }),
      makeOrder({
        orderId: 2,
        permId: 1002,
        symbol: "AAPL",
        filled: 4,
        remaining: 6,
        totalQuantity: 10,
      }),
    ];

    render(
      <CancelOrderDialog
        order={null}
        orders={orders}
        loading={false}
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );

    expect(
      screen.getByText(/One or more legs are partially filled/),
    ).toBeTruthy();
  });

  it("prefers orders over single order when both are provided", () => {
    const single = makeOrder({ symbol: "TSLA", orderId: 9, permId: 9 });
    const multi = [
      makeOrder({ symbol: "SPY", orderId: 1, permId: 1 }),
      makeOrder({ symbol: "SPY", orderId: 2, permId: 2 }),
    ];

    render(
      <CancelOrderDialog
        order={single}
        orders={multi}
        loading={false}
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );

    expect(screen.getByRole("heading", { name: "Cancel Orders" })).toBeTruthy();
    expect(screen.getByText("SPY")).toBeTruthy();
    expect(screen.queryByText("TSLA")).toBeNull();
    expect(screen.getByRole("button", { name: "Cancel All" })).toBeTruthy();
  });

  it("shows Cancelling... on the confirm button when loading", () => {
    const orders = [
      makeOrder({ orderId: 1, permId: 1 }),
      makeOrder({ orderId: 2, permId: 2 }),
    ];

    render(
      <CancelOrderDialog
        order={null}
        orders={orders}
        loading={true}
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );

    const confirm = screen.getByRole("button", { name: "Cancelling..." }) as HTMLButtonElement;
    const keep = screen.getByRole("button", { name: "Keep Orders" }) as HTMLButtonElement;
    expect(confirm).toBeTruthy();
    expect(confirm.disabled).toBe(true);
    expect(keep.disabled).toBe(true);
  });
});

describe("CancelOrderDialog — callbacks and copy", () => {
  it("calls onConfirm and onClose", () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    const order = makeOrder();

    render(
      <CancelOrderDialog
        order={order}
        loading={false}
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel Order" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Keep Order" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onConfirm for multi Cancel All", () => {
    const onConfirm = vi.fn();
    const orders = [
      makeOrder({ orderId: 1, permId: 1 }),
      makeOrder({ orderId: 2, permId: 2 }),
    ];

    render(
      <CancelOrderDialog
        order={null}
        orders={orders}
        loading={false}
        onConfirm={onConfirm}
        onClose={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel All" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("contains no em dashes in textContent (single)", () => {
    const order = makeOrder({
      filled: 2,
      remaining: 8,
      totalQuantity: 10,
    });

    const { container } = render(
      <CancelOrderDialog
        order={order}
        loading={false}
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );

    assertNoEmDash(container.textContent ?? "");
  });

  it("contains no em dashes in textContent (multi)", () => {
    const orders = [
      makeOrder({ orderId: 1, permId: 1, filled: 1, remaining: 9, totalQuantity: 10 }),
      makeOrder({ orderId: 2, permId: 2 }),
    ];

    const { container } = render(
      <CancelOrderDialog
        order={null}
        orders={orders}
        loading={false}
        onConfirm={() => {}}
        onClose={() => {}}
      />,
    );

    assertNoEmDash(container.textContent ?? "");
  });
});
