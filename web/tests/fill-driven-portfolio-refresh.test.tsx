/**
 * @vitest-environment jsdom
 *
 * Fill-driven portfolio invalidation (web/lib/useFillToasts.ts).
 *
 * A detected fill is the earliest signal the app has that positions changed.
 * Without this callback the portfolio snapshot only refreshed on its own
 * timer (60s producer run + up to 30s client poll), so a FILLED toast sat on
 * screen next to an unchanged position table.
 */

import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExecutedOrder, OrderContract, OrdersData } from "../lib/types";
import { SEEN_STORAGE_KEY } from "../lib/fillToasts";
import { useFillToasts } from "../lib/useFillToasts";

function makeContract(overrides: Partial<OrderContract> = {}): OrderContract {
  return {
    conId: 12345,
    symbol: "VIX",
    secType: "OPT",
    strike: 30,
    right: "C",
    expiry: "20261020",
    ...overrides,
  };
}

function makeFill(overrides: Partial<ExecutedOrder> = {}): ExecutedOrder {
  return {
    execId: "0000e0d5.665.01",
    symbol: "VIX",
    contract: makeContract(),
    side: "BOT",
    quantity: 2,
    avgPrice: 0.61,
    commission: null,
    realizedPNL: null,
    time: new Date().toISOString(),
    exchange: "SMART",
    ...overrides,
  };
}

function makeOrders(executed: ExecutedOrder[]): OrdersData {
  return {
    last_sync: new Date().toISOString(),
    open_orders: [],
    executed_orders: executed,
    open_count: 0,
    executed_count: executed.length,
  };
}

const BASELINE = [makeFill({ execId: "a.1.01" }), makeFill({ execId: "a.2.01" })];

type HookProps = { orders: OrdersData | null };

describe("useFillToasts onNewFills", () => {
  beforeEach(() => {
    window.sessionStorage.removeItem(SEEN_STORAGE_KEY);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("does not fire on the baseline payload", () => {
    const addToast = vi.fn();
    const onNewFills = vi.fn();
    renderHook(
      ({ orders }: HookProps) => useFillToasts(orders, addToast as never, onNewFills),
      { initialProps: { orders: makeOrders(BASELINE) } },
    );
    expect(onNewFills).not.toHaveBeenCalled();
  });

  it("fires once when new fills arrive and not again for the same fill", () => {
    const addToast = vi.fn();
    const onNewFills = vi.fn();
    const { rerender } = renderHook(
      ({ orders }: HookProps) => useFillToasts(orders, addToast as never, onNewFills),
      { initialProps: { orders: makeOrders(BASELINE) } },
    );
    expect(onNewFills).not.toHaveBeenCalled();

    rerender({ orders: makeOrders([...BASELINE, makeFill({ execId: "b.9.01" })]) });
    expect(onNewFills).toHaveBeenCalledTimes(1);

    rerender({ orders: makeOrders([...BASELINE, makeFill({ execId: "b.9.01" })]) });
    expect(onNewFills).toHaveBeenCalledTimes(1);
  });

  it("is optional: the hook still toasts without a callback", () => {
    const addToast = vi.fn();
    const { rerender } = renderHook(
      ({ orders }: HookProps) => useFillToasts(orders, addToast as never),
      { initialProps: { orders: makeOrders(BASELINE) } },
    );
    rerender({ orders: makeOrders([...BASELINE, makeFill({ execId: "c.1.01" })]) });
    expect(addToast).toHaveBeenCalledTimes(1);
  });
});
