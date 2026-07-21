/**
 * @vitest-environment jsdom
 *
 * Persistent fill toast — stateful hook (web/lib/useFillToasts.ts).
 *
 * Covers: baseline priming on the first non-null OrdersData (no toast storm),
 * exactly-one persistent toast per new execId, dedupe across rerenders,
 * sessionStorage survival across remounts, null-transition safety, and demo
 * suppression.
 */

import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExecutedOrder, OrderContract, OrdersData } from "../lib/types";
import { SEEN_STORAGE_KEY } from "../lib/fillToasts";
import { useFillToasts } from "../lib/useFillToasts";

function makeContract(overrides: Partial<OrderContract> = {}): OrderContract {
  return {
    conId: 12345,
    symbol: "EWY",
    secType: "OPT",
    strike: 175,
    right: "C",
    expiry: "20260918",
    ...overrides,
  };
}

function makeFill(overrides: Partial<ExecutedOrder> = {}): ExecutedOrder {
  return {
    execId: "0000e0d5.665.01",
    symbol: "EWY",
    contract: makeContract(),
    side: "SLD",
    quantity: 25,
    avgPrice: 4.55,
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

const BASELINE = [
  makeFill({ execId: "a.1.01" }),
  makeFill({ execId: "a.2.01" }),
  makeFill({ execId: "a.3.01" }),
];

type HookProps = { orders: OrdersData | null };

function mountHook(addToast: ReturnType<typeof vi.fn>, initialOrders: OrdersData | null = null) {
  return renderHook(
    ({ orders }: HookProps) => useFillToasts(orders, addToast as never),
    { initialProps: { orders: initialOrders } },
  );
}

describe("useFillToasts", () => {
  beforeEach(() => {
    window.sessionStorage.removeItem(SEEN_STORAGE_KEY);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("does not toast the first non-null payload (baseline snapshot)", () => {
    const addToast = vi.fn();
    const { rerender } = mountHook(addToast);
    rerender({ orders: makeOrders(BASELINE) });
    expect(addToast).not.toHaveBeenCalled();
  });

  it("toasts exactly once for a new fill, persistent (duration 0)", () => {
    const addToast = vi.fn();
    const { rerender } = mountHook(addToast);
    rerender({ orders: makeOrders(BASELINE) });

    const newFill = makeFill({ execId: "b.9.01" });
    rerender({ orders: makeOrders([...BASELINE, newFill]) });

    expect(addToast).toHaveBeenCalledTimes(1);
    expect(addToast).toHaveBeenCalledWith("success", "FILLED · SELL 25x EWY $175C @ $4.55", 0);
  });

  it("does not re-toast on rerender with identical data", () => {
    const addToast = vi.fn();
    const { rerender } = mountHook(addToast);
    rerender({ orders: makeOrders(BASELINE) });

    const withNew = [...BASELINE, makeFill({ execId: "b.9.01" })];
    rerender({ orders: makeOrders(withNew) });
    rerender({ orders: makeOrders(withNew) });
    rerender({ orders: makeOrders(withNew) });

    expect(addToast).toHaveBeenCalledTimes(1);
  });

  it("does not re-toast a seen fill after unmount + remount (sessionStorage survival)", () => {
    const addToast = vi.fn();
    const first = mountHook(addToast);
    first.rerender({ orders: makeOrders(BASELINE) });
    const newFill = makeFill({ execId: "b.9.01" });
    first.rerender({ orders: makeOrders([...BASELINE, newFill]) });
    expect(addToast).toHaveBeenCalledTimes(1);
    first.unmount();

    const addToast2 = vi.fn();
    const second = mountHook(addToast2);
    second.rerender({ orders: makeOrders([...BASELINE, newFill]) });
    expect(addToast2).not.toHaveBeenCalled();
  });

  it("toasts a fill that landed while unmounted (route-nav does not swallow fills)", () => {
    const addToast = vi.fn();
    const first = mountHook(addToast);
    first.rerender({ orders: makeOrders(BASELINE) });
    expect(addToast).not.toHaveBeenCalled();
    first.unmount();

    // Fill executes during navigation; the remounted hook's FIRST payload
    // already contains it. A re-prime would silently absorb it.
    const midNavFill = makeFill({ execId: "c.7.01" });
    const addToast2 = vi.fn();
    const second = mountHook(addToast2);
    second.rerender({ orders: makeOrders([...BASELINE, midNavFill]) });

    expect(addToast2).toHaveBeenCalledTimes(1);
    expect(addToast2).toHaveBeenCalledWith("success", "FILLED · SELL 25x EWY $175C @ $4.55", 0);
  });

  it("does not re-toast seen fills after orders transitions to null and back", () => {
    const addToast = vi.fn();
    const { rerender } = mountHook(addToast);
    rerender({ orders: makeOrders(BASELINE) });
    const withNew = [...BASELINE, makeFill({ execId: "b.9.01" })];
    rerender({ orders: makeOrders(withNew) });
    expect(addToast).toHaveBeenCalledTimes(1);

    rerender({ orders: null });
    rerender({ orders: makeOrders(withNew) });
    expect(addToast).toHaveBeenCalledTimes(1);
  });

  it("never toasts in demo mode", () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    const addToast = vi.fn();
    const { rerender } = mountHook(addToast);
    rerender({ orders: makeOrders(BASELINE) });
    rerender({ orders: makeOrders([...BASELINE, makeFill({ execId: "b.9.01" })]) });
    expect(addToast).not.toHaveBeenCalled();
  });
});
