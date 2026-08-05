/**
 * @vitest-environment jsdom
 *
 * Mobile open-order card UX: partial fill qty, intent-based card tone,
 * and non-empty action sheet summary.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, cleanup, screen, fireEvent } from "@testing-library/react";
import MobileOrderList from "../components/mobile/MobileOrderList";
import type { OpenOrder, PortfolioPosition } from "../lib/types";
import type { OpenOrderDisplayRow } from "../lib/openOrderCombos";

afterEach(() => cleanup());

const EM_DASH = /—/;

function makeOrder(overrides: Partial<OpenOrder> = {}): OpenOrder {
  const totalQuantity = overrides.totalQuantity ?? 10;
  return {
    orderId: overrides.orderId ?? 1,
    permId: overrides.permId ?? 1001,
    symbol: overrides.symbol ?? "AAPL",
    contract: overrides.contract ?? {
      conId: 1,
      symbol: "AAPL",
      secType: "OPT",
      strike: 200,
      right: "C",
      expiry: "2026-08-21",
    },
    action: overrides.action ?? "BUY",
    orderType: overrides.orderType ?? "LMT",
    totalQuantity,
    limitPrice: overrides.limitPrice ?? 1.5,
    auxPrice: overrides.auxPrice ?? null,
    status: overrides.status ?? "Submitted",
    filled: overrides.filled ?? 0,
    remaining: overrides.remaining ?? totalQuantity,
    avgFillPrice: overrides.avgFillPrice ?? null,
    tif: overrides.tif ?? "DAY",
  };
}

function singleRow(order: OpenOrder): OpenOrderDisplayRow {
  return {
    kind: "single",
    index: 0,
    summary: null,
    order,
  };
}

function comboRow(orders: OpenOrder[]): OpenOrderDisplayRow {
  return {
    kind: "combo",
    id: "combo-AAPL-1",
    index: 0,
    symbol: "AAPL",
    structure: "Call Spread",
    summary: "Call Spread (Long Call 200 / Short Call 210)",
    orders,
    totalQuantity: orders[0]?.totalQuantity ?? 1,
    orderType: "BAG",
    status: orders[0]?.status ?? "Submitted",
    tif: orders[0]?.tif ?? "DAY",
    limitPrice: orders[0]?.limitPrice ?? null,
  };
}

function longOptPosition(): PortfolioPosition {
  return {
    id: 2,
    ticker: "AAPL",
    structure: "Call",
    structure_type: "option",
    risk_profile: "defined",
    expiry: "2026-08-21",
    contracts: 5,
    direction: "LONG",
    entry_cost: 750,
    max_risk: 750,
    market_value: 750,
    legs: [
      {
        type: "Call",
        strike: 200,
        direction: "LONG",
        contracts: 5,
        entry_cost: 150,
        avg_cost: 150,
        market_price: 1.5,
        market_value: 750,
      },
    ],
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-01-01",
  };
}

const noop = () => undefined;

describe("MobileOrderList display", () => {
  it("shows partial fill qty as filled/total on the card", () => {
    const order = makeOrder({
      filled: 3,
      remaining: 7,
      totalQuantity: 10,
      status: "Submitted",
    });
    render(
      <MobileOrderList
        rows={[singleRow(order)]}
        canModify={() => true}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );

    const card = screen.getByTestId("mobile-order-single-1001");
    expect(card.textContent).toContain("3/10");
    expect(card.textContent).toContain("Partial");
    expect(card.textContent).not.toMatch(EM_DASH);
  });

  it("maps Submitted status to Working on the card", () => {
    const order = makeOrder({ filled: 0, remaining: 10, status: "Submitted" });
    render(
      <MobileOrderList
        rows={[singleRow(order)]}
        canModify={() => true}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );
    const card = screen.getByTestId("mobile-order-single-1001");
    expect(card.textContent).toContain("Working");
    expect(card.textContent).not.toContain("Submitted");
  });

  it("CLOSE intent uses default tone (no forced positive/negative for SELL-to-close)", () => {
    const order = makeOrder({
      action: "SELL",
      filled: 0,
      remaining: 5,
      totalQuantity: 5,
    });
    render(
      <MobileOrderList
        rows={[singleRow(order)]}
        portfolioPositions={[longOptPosition()]}
        canModify={() => true}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );
    const card = screen.getByTestId("mobile-order-single-1001");
    expect(card.className).not.toContain("mobile-card--negative");
    expect(card.className).not.toContain("mobile-card--positive");
  });

  it("OPEN SELL still uses negative tone when no portfolio coverage", () => {
    const order = makeOrder({
      action: "SELL",
      filled: 0,
      remaining: 5,
      totalQuantity: 5,
    });
    render(
      <MobileOrderList
        rows={[singleRow(order)]}
        portfolioPositions={[]}
        canModify={() => true}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );
    const card = screen.getByTestId("mobile-order-single-1001");
    expect(card.className).toContain("mobile-card--negative");
  });

  it("tones the side token on single-order cards (SELL negative, BUY positive)", () => {
    const sell = makeOrder({ permId: 1001, action: "SELL" });
    const buy = makeOrder({ permId: 1002, orderId: 2, action: "BUY" });
    render(
      <MobileOrderList
        rows={[singleRow(sell), singleRow(buy)]}
        canModify={() => true}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );

    const sides = screen.getAllByTestId("mobile-order-side");
    expect(sides).toHaveLength(2);
    expect(sides[0].textContent).toBe("SELL");
    expect(sides[0].className).toContain("m-order-side--sell");
    expect(sides[1].textContent).toBe("BUY");
    expect(sides[1].className).toContain("m-order-side--buy");
  });

  it("combo cards keep the structure title without a side token", () => {
    const legs = [
      makeOrder({ permId: 10, action: "BUY" }),
      makeOrder({ permId: 11, orderId: 2, action: "SELL" }),
    ];
    render(
      <MobileOrderList
        rows={[comboRow(legs)]}
        canModify={() => true}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );
    const card = screen.getByTestId("mobile-order-combo-AAPL-1");
    expect(card.textContent).toContain("Call Spread");
    expect(screen.queryByTestId("mobile-order-side")).toBeNull();
  });

  it("renders status as a chip with a warning tone for partial fills", () => {
    const order = makeOrder({ filled: 3, remaining: 7, totalQuantity: 10 });
    render(
      <MobileOrderList
        rows={[singleRow(order)]}
        canModify={() => true}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );
    const status = screen.getByTestId("mobile-order-status");
    expect(status.textContent).toBe("Partial");
    expect(status.className).toContain("m-order-status");
    expect(status.className).toContain("m-order-status--warn");
  });

  it("renders a neutral status chip for working orders", () => {
    const order = makeOrder({ filled: 0, remaining: 10, status: "Submitted" });
    render(
      <MobileOrderList
        rows={[singleRow(order)]}
        canModify={() => true}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );
    const status = screen.getByTestId("mobile-order-status");
    expect(status.textContent).toBe("Working");
    expect(status.className).toContain("m-order-status");
    expect(status.className).not.toContain("m-order-status--warn");
  });

  it("action sheet shows summary content when card is clicked", () => {
    const order = makeOrder({
      filled: 3,
      remaining: 7,
      totalQuantity: 10,
      limitPrice: 2.25,
      status: "Submitted",
      tif: "GTC",
    });
    render(
      <MobileOrderList
        rows={[singleRow(order)]}
        canModify={() => true}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );

    fireEvent.click(screen.getByTestId("mobile-order-single-1001"));

    const summary = screen.getByTestId("mobile-order-sheet-summary");
    expect(summary).toBeTruthy();
    expect(summary.textContent).toContain("3/10");
    expect(summary.textContent).toContain("Partial");
    expect(summary.textContent).toContain("GTC");
    expect(summary.textContent).toMatch(/2\.25|2\.2500/);
    expect(screen.getByTestId("mobile-order-action-cancel").textContent).toBe(
      "Cancel order",
    );
    expect(screen.getByTestId("mobile-order-action-modify").textContent).toBe(
      "Modify limit price",
    );
    expect(summary.textContent).not.toMatch(EM_DASH);
  });

  it("shows OPEN intent badge when known", () => {
    const order = makeOrder({ action: "BUY" });
    render(
      <MobileOrderList
        rows={[singleRow(order)]}
        portfolioPositions={[]}
        canModify={() => true}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );
    fireEvent.click(screen.getByTestId("mobile-order-single-1001"));
    expect(screen.getByTestId("mobile-order-intent-badge").textContent).toBe("OPEN");
  });

  it("shows CLOSE intent badge for sell-to-close", () => {
    const order = makeOrder({ action: "SELL" });
    render(
      <MobileOrderList
        rows={[singleRow(order)]}
        portfolioPositions={[longOptPosition()]}
        canModify={() => true}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );
    fireEvent.click(screen.getByTestId("mobile-order-single-1001"));
    expect(screen.getByTestId("mobile-order-intent-badge").textContent).toBe("CLOSE");
  });

  it("combo cancel label is Cancel all legs and routes through onRequestCancel", () => {
    const onRequestCancel = vi.fn();
    const legs = [
      makeOrder({
        permId: 10,
        action: "BUY",
        totalQuantity: 2,
        filled: 0,
        remaining: 2,
        limitPrice: 1.0,
        contract: {
          conId: 1,
          symbol: "AAPL",
          secType: "OPT",
          strike: 200,
          right: "C",
          expiry: "2026-08-21",
        },
      }),
      makeOrder({
        permId: 11,
        orderId: 2,
        action: "SELL",
        totalQuantity: 2,
        filled: 0,
        remaining: 2,
        limitPrice: 0.5,
        contract: {
          conId: 2,
          symbol: "AAPL",
          secType: "OPT",
          strike: 210,
          right: "C",
          expiry: "2026-08-21",
        },
      }),
    ];
    render(
      <MobileOrderList
        rows={[comboRow(legs)]}
        canModify={() => true}
        onRequestCancel={onRequestCancel}
        onRequestModify={noop}
      />,
    );

    fireEvent.click(screen.getByTestId("mobile-order-combo-AAPL-1"));

    const summary = screen.getByTestId("mobile-order-sheet-summary");
    expect(summary.textContent).toContain("Call Spread");
    expect(screen.getByTestId("mobile-order-sheet-legs").textContent).toMatch(/BUY/);
    expect(screen.getByTestId("mobile-order-sheet-legs").textContent).toMatch(/SELL/);

    const cancelBtn = screen.getByTestId("mobile-order-action-cancel");
    expect(cancelBtn.textContent).toBe("Cancel all legs");
    fireEvent.click(cancelBtn);
    expect(onRequestCancel).toHaveBeenCalledTimes(1);
    expect(onRequestCancel.mock.calls[0][0]).toBeNull();
    expect(onRequestCancel.mock.calls[0][1]).toHaveLength(2);
  });

  it("renders no em dashes in card or sheet markup", () => {
    const order = makeOrder({
      filled: 3,
      remaining: 7,
      totalQuantity: 10,
      limitPrice: null,
      orderType: "STP",
    });
    const { container } = render(
      <MobileOrderList
        rows={[singleRow(order)]}
        canModify={() => true}
        onRequestCancel={noop}
        onRequestModify={noop}
      />,
    );
    fireEvent.click(screen.getByTestId("mobile-order-single-1001"));
    expect(container.textContent ?? "").not.toMatch(EM_DASH);
  });
});
