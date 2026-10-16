// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PortfolioPosition } from "../lib/types";
import { suggestStrategyGroups } from "../lib/positionStrategyGroups";
import { POSITION_STRATEGY_GROUPS_STORAGE_KEY as KEY, usePositionStrategyGroups } from "../lib/usePositionStrategyGroups";

function source(account: string | null = "DU123"): PortfolioPosition {
  return {
    id: 42, account_id: account, ticker: "AAOI", structure: "Combo (3 legs)", structure_type: "Combo",
    risk_profile: "undefined", expiry: "2026-11-20", contracts: 60, direction: "COMBO", entry_cost: 0,
    market_value: 0, max_risk: null, kelly_optimal: null, target: null, stop: null,
    entry_date: "2026-10-13", basis_source: "ib",
    legs: [
      { con_id: 101, type: "Call", strike: 105, direction: "LONG", contracts: 60, entry_cost: 6000, avg_cost: 100, market_price: 1, market_value: 6000 },
      { con_id: 102, type: "Call", strike: 140, direction: "SHORT", contracts: 60, entry_cost: 3000, avg_cost: 50, market_price: 0.5, market_value: 3000 },
      { con_id: 103, type: "Put", strike: 90, direction: "SHORT", contracts: 50, entry_cost: 3000, avg_cost: 60, market_price: 0.6, market_value: 3000 },
    ],
  };
}

beforeEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

describe("position strategy browser preferences", () => {
  it("restores groups after reload and preserves raw broker sources", () => {
    const original = source();
    const first = renderHook(() => usePositionStrategyGroups([original]));
    act(() => { expect(first.result.current.apply(original, suggestStrategyGroups(original)!)).toBe(true); });
    expect(first.result.current.positions).toHaveLength(2);
    const stored = JSON.parse(localStorage.getItem(KEY)!);
    expect(stored.version).toBe(1);
    first.unmount();
    const refreshed = { ...original, id: 999, legs: [...original.legs].reverse() };
    const second = renderHook(() => usePositionStrategyGroups([refreshed]));
    expect(second.result.current.positions).toHaveLength(2);
    expect(second.result.current.positions.map(row => row.contracts)).toEqual([60, 50]);
    for (const row of second.result.current.positions) expect(second.result.current.sourceFor(row)).toBe(refreshed);
  });

  it("falls back to canonical rows and rejects stale editor commits after broker changes", () => {
    const original = source();
    const hook = renderHook(({ positions }) => usePositionStrategyGroups(positions), { initialProps: { positions: [original] } });
    act(() => { hook.result.current.apply(original, suggestStrategyGroups(original)!); });
    const changed = { ...original, legs: original.legs.map((leg, index) => ({ ...leg, contracts: leg.contracts + (index === 0 ? 1 : 0) })) };
    hook.rerender({ positions: [changed] });
    expect(hook.result.current.positions).toEqual([changed]);
    act(() => { expect(hook.result.current.apply(original, suggestStrategyGroups(original)!)).toBe(false); });
    expect(hook.result.current.error).toMatch(/position changed/i);
  });

  it("retains the previous layout on storage failure, then persists undo", () => {
    const original = source();
    const hook = renderHook(() => usePositionStrategyGroups([original]));
    const write = vi.spyOn(window.localStorage, "setItem").mockImplementation(() => { throw new Error("quota"); });
    act(() => { expect(hook.result.current.apply(original, suggestStrategyGroups(original)!)).toBe(false); });
    expect(hook.result.current.positions).toEqual([original]);
    expect(hook.result.current.error).toMatch(/could not be saved/i);
    write.mockRestore();
    act(() => { hook.result.current.apply(original, suggestStrategyGroups(original)!); });
    expect(hook.result.current.canUndo).toBe(true);
    act(() => { expect(hook.result.current.undo()).toBe(true); });
    expect(hook.result.current.positions).toEqual([original]);
    expect(hook.result.current.canUndo).toBe(false);
    expect(JSON.parse(localStorage.getItem(KEY)!).plans).toEqual({});
  });

  it("never stores accountless layouts or restores another account's layout", () => {
    const unknown = source(null);
    const hook = renderHook(() => usePositionStrategyGroups([unknown]));
    act(() => { hook.result.current.apply(unknown, suggestStrategyGroups(unknown)!); });
    expect(hook.result.current.positions).toHaveLength(2);
    expect(localStorage.getItem(KEY)).toBeNull();
    hook.unmount();
    const known = source();
    const knownHook = renderHook(() => usePositionStrategyGroups([known]));
    act(() => { knownHook.result.current.apply(known, suggestStrategyGroups(known)!); });
    knownHook.unmount();
    const other = source("DU456");
    const otherHook = renderHook(() => usePositionStrategyGroups([other]));
    expect(otherHook.result.current.positions).toEqual([other]);
  });

  it("isolates identical ticker positions across accounts and canonical row IDs", () => {
    const first = source();
    const second = source("DU456");
    const hook = renderHook(() => usePositionStrategyGroups([first, second]));
    expect(hook.result.current.sourceFor(first)).toBe(first);
    expect(hook.result.current.sourceFor(second)).toBe(second);
    act(() => { hook.result.current.apply(first, suggestStrategyGroups(first)!); hook.result.current.apply(second, suggestStrategyGroups(second)!); });
    const ids = hook.result.current.positions.map(row => row.id);
    expect(new Set(ids).size).toBe(4);
    expect(hook.result.current.positions.map(row => hook.result.current.sourceFor(row).account_id)).toEqual(["DU123", "DU123", "DU456", "DU456"]);
  });

  it("handles malformed stored data and reset without touching broker rows", () => {
    localStorage.setItem(KEY, "{");
    const original = source();
    const hook = renderHook(() => usePositionStrategyGroups([original]));
    expect(hook.result.current.error).toMatch(/could not be loaded/i);
    act(() => { hook.result.current.apply(original, suggestStrategyGroups(original)!); });
    act(() => { expect(hook.result.current.reset(original)).toBe(true); });
    expect(hook.result.current.positions).toEqual([original]);
  });

  it("refuses an ambiguous duplicate source instead of applying one plan to both", () => {
    const original = source();
    const duplicate = { ...source(), id: 999 };
    const hook = renderHook(() => usePositionStrategyGroups([original, duplicate]));
    act(() => { expect(hook.result.current.apply(original, suggestStrategyGroups(original)!)).toBe(false); });
    expect(hook.result.current.positions).toEqual([original, duplicate]);
  });
});
