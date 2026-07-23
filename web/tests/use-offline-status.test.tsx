/**
 * @vitest-environment jsdom
 *
 * <OfflineStatusProvider> / useOfflineStatus() — browser events + signals bus
 * drive the pure reducer. Fake timers pin the 2s navigator-offline debounce.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { OfflineStatusProvider, useOfflineStatus } from "../lib/offline/OfflineStatusContext";
import {
  reportFetchFailure,
  reportFetchSuccess,
  reportOfflineServed,
} from "../lib/offline/offlineSignals";

const CACHED_AT_FIXTURE = 1_700_000_000_000;

function wrapper({ children }: { children: React.ReactNode }) {
  return <OfflineStatusProvider>{children}</OfflineStatusProvider>;
}

function renderStatus() {
  return renderHook(() => useOfflineStatus(), { wrapper });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useOfflineStatus", () => {
  it("starts online", () => {
    const { result } = renderStatus();
    expect(result.current.offline).toBe(false);
    expect(result.current.cachedAt).toBeNull();
  });

  it("navigator offline event trips only after the 2s debounce", () => {
    const { result } = renderStatus();
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(result.current.offline).toBe(false);
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(result.current.offline).toBe(true);
  });

  it("a transient blip (online within the debounce) never trips", () => {
    const { result } = renderStatus();
    act(() => {
      window.dispatchEvent(new Event("offline"));
    });
    act(() => {
      vi.advanceTimersByTime(1000);
      window.dispatchEvent(new Event("online"));
    });
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(result.current.offline).toBe(false);
  });

  it("online event clears offline immediately", () => {
    const { result } = renderStatus();
    act(() => {
      window.dispatchEvent(new Event("offline"));
      vi.advanceTimersByTime(2000);
    });
    expect(result.current.offline).toBe(true);
    act(() => {
      window.dispatchEvent(new Event("online"));
    });
    expect(result.current.offline).toBe(false);
  });

  it("reportOfflineServed enters after the 2s delay with cachedAt (no instant flash)", () => {
    const { result } = renderStatus();
    act(() => {
      reportOfflineServed(CACHED_AT_FIXTURE);
    });
    expect(result.current.offline).toBe(false);
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(result.current.offline).toBe(true);
    expect(result.current.cachedAt).toBe(CACHED_AT_FIXTURE);
  });

  it("a single fetch failure does not trip", () => {
    const { result } = renderStatus();
    act(() => {
      reportFetchFailure();
    });
    expect(result.current.offline).toBe(false);
  });

  it("two fetch failures within the window trip", () => {
    const { result } = renderStatus();
    act(() => {
      reportFetchFailure();
      reportFetchFailure();
    });
    expect(result.current.offline).toBe(true);
  });

  it("reportFetchSuccess while offline clears immediately", () => {
    const { result } = renderStatus();
    act(() => {
      reportOfflineServed(CACHED_AT_FIXTURE);
      vi.advanceTimersByTime(2000);
    });
    expect(result.current.offline).toBe(true);
    act(() => {
      reportFetchSuccess();
    });
    expect(result.current.offline).toBe(false);
  });
});
