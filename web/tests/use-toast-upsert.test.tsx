/**
 * @vitest-environment jsdom
 *
 * Keyed toast coalescing (web/lib/useToast.ts).
 *
 * Covers: one toast per key with in-place message updates, independence
 * between keys, auto-dismiss timer restart on update, and a dismissed toast
 * not being resurrected by a later update for the same key.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useToast } from "../lib/useToast";

describe("useToast upsertToast", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("updates the existing toast for a key instead of stacking a new one", () => {
    const { result } = renderHook(() => useToast());

    act(() => {
      result.current.upsertToast("order:77", "success", "FILLED · SELL 10x EWY $175C @ $4.50", 0);
    });
    act(() => {
      result.current.upsertToast("order:77", "success", "FILLED · SELL 25x EWY $175C @ $4.56", 0);
    });

    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].message).toBe("FILLED · SELL 25x EWY $175C @ $4.56");
  });

  it("keeps a stable id across updates so the toast does not re-animate", () => {
    const { result } = renderHook(() => useToast());

    let firstId = "";
    act(() => {
      firstId = result.current.upsertToast("order:77", "success", "one", 0);
    });
    let secondId = "";
    act(() => {
      secondId = result.current.upsertToast("order:77", "success", "two", 0);
    });

    expect(secondId).toBe(firstId);
  });

  it("keeps separate keys on separate toasts", () => {
    const { result } = renderHook(() => useToast());

    act(() => {
      result.current.upsertToast("order:11", "success", "one", 0);
      result.current.upsertToast("order:12", "success", "two", 0);
    });

    expect(result.current.toasts.map((t) => t.message)).toEqual(["one", "two"]);
  });

  it("restarts the auto-dismiss timer on update", () => {
    const { result } = renderHook(() => useToast());

    act(() => {
      result.current.upsertToast("order:77", "success", "one", 5000);
    });
    act(() => {
      vi.advanceTimersByTime(4000);
      result.current.upsertToast("order:77", "success", "two", 5000);
    });
    act(() => {
      vi.advanceTimersByTime(4000);
    });

    expect(result.current.toasts).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(2000);
      vi.advanceTimersByTime(200);
    });
    expect(result.current.toasts).toHaveLength(0);
  });

  it("opens a fresh toast after the operator dismissed the previous one", () => {
    const { result } = renderHook(() => useToast());

    act(() => {
      result.current.upsertToast("order:77", "success", "one", 0);
    });
    act(() => {
      result.current.dismissToast(result.current.toasts[0].id);
      vi.advanceTimersByTime(200);
    });
    expect(result.current.toasts).toHaveLength(0);

    act(() => {
      result.current.upsertToast("order:77", "success", "two", 0);
    });
    expect(result.current.toasts.map((t) => t.message)).toEqual(["two"]);
  });
});
