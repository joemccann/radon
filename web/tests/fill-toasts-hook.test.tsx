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

function mountHook(upsertToast: ReturnType<typeof vi.fn>, initialOrders: OrdersData | null = null) {
  return renderHook(
    ({ orders }: HookProps) => useFillToasts(orders, upsertToast as never),
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
    const upsertToast = vi.fn();
    const { rerender } = mountHook(upsertToast);
    rerender({ orders: makeOrders(BASELINE) });
    expect(upsertToast).not.toHaveBeenCalled();
  });

  it("toasts exactly once for a new fill, persistent (duration 0)", () => {
    const upsertToast = vi.fn();
    const { rerender } = mountHook(upsertToast);
    rerender({ orders: makeOrders(BASELINE) });

    const newFill = makeFill({ execId: "b.9.01" });
    rerender({ orders: makeOrders([...BASELINE, newFill]) });

    expect(upsertToast).toHaveBeenCalledTimes(1);
    expect(upsertToast).toHaveBeenCalledWith(expect.any(String), "success", "FILLED · SELL 25x EWY $175C @ $4.55", 0);
  });

  it("does not re-toast on rerender with identical data", () => {
    const upsertToast = vi.fn();
    const { rerender } = mountHook(upsertToast);
    rerender({ orders: makeOrders(BASELINE) });

    const withNew = [...BASELINE, makeFill({ execId: "b.9.01" })];
    rerender({ orders: makeOrders(withNew) });
    rerender({ orders: makeOrders(withNew) });
    rerender({ orders: makeOrders(withNew) });

    expect(upsertToast).toHaveBeenCalledTimes(1);
  });

  it("does not re-toast a seen fill after unmount + remount (sessionStorage survival)", () => {
    const upsertToast = vi.fn();
    const first = mountHook(upsertToast);
    first.rerender({ orders: makeOrders(BASELINE) });
    const newFill = makeFill({ execId: "b.9.01" });
    first.rerender({ orders: makeOrders([...BASELINE, newFill]) });
    expect(upsertToast).toHaveBeenCalledTimes(1);
    first.unmount();

    const upsertToast2 = vi.fn();
    const second = mountHook(upsertToast2);
    second.rerender({ orders: makeOrders([...BASELINE, newFill]) });
    expect(upsertToast2).not.toHaveBeenCalled();
  });

  it("toasts a fill that landed while unmounted (route-nav does not swallow fills)", () => {
    const upsertToast = vi.fn();
    const first = mountHook(upsertToast);
    first.rerender({ orders: makeOrders(BASELINE) });
    expect(upsertToast).not.toHaveBeenCalled();
    first.unmount();

    // Fill executes during navigation; the remounted hook's FIRST payload
    // already contains it. A re-prime would silently absorb it.
    const midNavFill = makeFill({ execId: "c.7.01" });
    const upsertToast2 = vi.fn();
    const second = mountHook(upsertToast2);
    second.rerender({ orders: makeOrders([...BASELINE, midNavFill]) });

    expect(upsertToast2).toHaveBeenCalledTimes(1);
    expect(upsertToast2).toHaveBeenCalledWith(expect.any(String), "success", "FILLED · SELL 25x EWY $175C @ $4.55", 0);
  });

  it("does not re-toast seen fills after orders transitions to null and back", () => {
    const upsertToast = vi.fn();
    const { rerender } = mountHook(upsertToast);
    rerender({ orders: makeOrders(BASELINE) });
    const withNew = [...BASELINE, makeFill({ execId: "b.9.01" })];
    rerender({ orders: makeOrders(withNew) });
    expect(upsertToast).toHaveBeenCalledTimes(1);

    rerender({ orders: null });
    rerender({ orders: makeOrders(withNew) });
    expect(upsertToast).toHaveBeenCalledTimes(1);
  });

  it("coalesces partial fills of one order into a single accumulating toast", () => {
    const upsertToast = vi.fn();
    const { rerender } = mountHook(upsertToast);
    rerender({ orders: makeOrders(BASELINE) });

    const first = makeFill({ execId: "p.1.01", orderId: 77, quantity: 10, avgPrice: 4.5 });
    rerender({ orders: makeOrders([...BASELINE, first]) });
    const second = makeFill({ execId: "p.2.01", orderId: 77, quantity: 15, avgPrice: 4.6 });
    rerender({ orders: makeOrders([...BASELINE, first, second]) });

    expect(upsertToast).toHaveBeenCalledTimes(2);
    const [firstKey] = upsertToast.mock.calls[0] as [string];
    const [secondKey, , message] = upsertToast.mock.calls[1] as [string, string, string];
    expect(secondKey).toBe(firstKey);
    expect(message).toBe("FILLED · SELL 25x EWY $175C @ $4.56");
  });

  it("keeps separate orders on separate toasts", () => {
    const upsertToast = vi.fn();
    const { rerender } = mountHook(upsertToast);
    rerender({ orders: makeOrders(BASELINE) });

    const a = makeFill({ execId: "q.1.01", orderId: 11 });
    const b = makeFill({ execId: "q.2.01", orderId: 12 });
    rerender({ orders: makeOrders([...BASELINE, a, b]) });

    const keys = upsertToast.mock.calls.map((call) => call[0] as string);
    expect(new Set(keys).size).toBe(2);
  });

  it("starts a fresh running total after the group's toast is dismissed (R-642)", () => {
    // Dismiss "FILLED BUY 900x" at 900/1000; the last 100 lots must toast as
    // 100, not resurrect the cumulative 1000 as if it were a new fill.
    const live = new Set<string>();
    const upsertToast = vi.fn((key: string) => {
      live.add(key);
      return key;
    });
    const isToastLive = (key: string) => live.has(key);
    const { rerender } = renderHook(
      ({ orders }: HookProps) =>
        useFillToasts(orders, upsertToast as never, undefined, isToastLive),
      { initialProps: { orders: null as OrdersData | null } },
    );
    rerender({ orders: makeOrders(BASELINE) });

    const first = makeFill({ execId: "d.1.01", permId: 9001, side: "BOT", quantity: 900, avgPrice: 4.5 });
    rerender({ orders: makeOrders([...BASELINE, first]) });
    expect(upsertToast).toHaveBeenCalledTimes(1);
    const [groupKey, , firstMsg] = upsertToast.mock.calls[0] as [string, string, string];
    expect(firstMsg).toContain("900x");

    live.delete(groupKey); // operator dismissed the toast

    const last = makeFill({ execId: "d.2.01", permId: 9001, side: "BOT", quantity: 100, avgPrice: 4.5 });
    rerender({ orders: makeOrders([...BASELINE, first, last]) });
    expect(upsertToast).toHaveBeenCalledTimes(2);
    const [, , secondMsg] = upsertToast.mock.calls[1] as [string, string, string];
    expect(secondMsg).toContain("100x");
    expect(secondMsg).not.toContain("1000x");
  });

  it("does not merge a recycled orderId across different permIds (R-642)", () => {
    const upsertToast = vi.fn();
    const { rerender } = mountHook(upsertToast);
    rerender({ orders: makeOrders(BASELINE) });

    const morning = makeFill({ execId: "m.1.01", orderId: 42, permId: 111, quantity: 10 });
    rerender({ orders: makeOrders([...BASELINE, morning]) });
    const afternoon = makeFill({ execId: "m.2.01", orderId: 42, permId: 222, quantity: 5 });
    rerender({ orders: makeOrders([...BASELINE, morning, afternoon]) });

    expect(upsertToast).toHaveBeenCalledTimes(2);
    const keys = upsertToast.mock.calls.map((call) => call[0] as string);
    expect(new Set(keys).size).toBe(2);
    const [, , secondMsg] = upsertToast.mock.calls[1] as [string, string, string];
    expect(secondMsg).toContain("5x");
    expect(secondMsg).not.toContain("15x");
  });

  it("never toasts in demo mode", () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    const upsertToast = vi.fn();
    const { rerender } = mountHook(upsertToast);
    rerender({ orders: makeOrders(BASELINE) });
    rerender({ orders: makeOrders([...BASELINE, makeFill({ execId: "b.9.01" })]) });
    expect(upsertToast).not.toHaveBeenCalled();
  });
});
