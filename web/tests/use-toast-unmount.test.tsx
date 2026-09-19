/**
 * @vitest-environment jsdom
 *
 * useToast timers must not outlive the component (web/lib/useToast.ts).
 *
 * A pending auto-dismiss or exit-animation timer that fires after unmount
 * calls setState on a dead hook. In CI that landed after the jsdom
 * environment was torn down, so React's `window.event` read threw
 * "ReferenceError: window is not defined" as an unhandled error and failed a
 * shard whose tests had all passed.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useToast } from "../lib/useToast";

/** Fire every pending timer with `window` gone, as after jsdom teardown. */
function fireTimersWithoutWindow() {
  vi.stubGlobal("window", undefined);
  try {
    vi.advanceTimersByTime(10_000);
  } finally {
    vi.unstubAllGlobals();
  }
}

describe("useToast unmount", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("clears the pending auto-dismiss timer on unmount", () => {
    const { result, unmount } = renderHook(() => useToast());

    act(() => {
      result.current.addToast("success", "FILLED", 5000);
    });
    expect(vi.getTimerCount()).toBe(1);

    unmount();

    expect(vi.getTimerCount()).toBe(0);
    expect(fireTimersWithoutWindow).not.toThrow();
  });

  it("clears the pending exit-animation timer on unmount", () => {
    const { result, unmount } = renderHook(() => useToast());

    let id = "";
    act(() => {
      id = result.current.addToast("success", "FILLED", 0);
    });
    act(() => {
      result.current.dismissToast(id);
    });
    expect(vi.getTimerCount()).toBe(1);

    unmount();

    expect(vi.getTimerCount()).toBe(0);
    expect(fireTimersWithoutWindow).not.toThrow();
  });

  it("still removes a dismissed toast after the exit animation while mounted", () => {
    const { result } = renderHook(() => useToast());

    let id = "";
    act(() => {
      id = result.current.addToast("success", "FILLED", 0);
    });
    act(() => {
      result.current.dismissToast(id);
    });
    expect(result.current.exitingIds.has(id)).toBe(true);

    act(() => {
      vi.advanceTimersByTime(150);
    });

    expect(result.current.toasts).toHaveLength(0);
    expect(result.current.exitingIds.has(id)).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });
});
