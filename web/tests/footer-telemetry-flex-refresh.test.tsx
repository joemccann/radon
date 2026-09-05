/**
 * @vitest-environment jsdom
 *
 * R-664: the Flex Token chip fetched /api/flex-token exactly once on mount.
 * A workspace tab left open across days kept showing a stale countdown (or
 * "Active" past expiry). The chip must refetch on window focus and on a
 * periodic timer.
 */

import { cleanup, render, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/IBStatusContext", () => ({
  useIBStatusContext: () => ({ displayStatus: "connected" }),
}));

vi.mock("@/lib/useServiceHealth", () => ({
  useServiceHealth: () => ({ data: null, loading: false, error: null }),
}));

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("FooterTelemetryStrip flex chip refresh", () => {
  it("refetches /api/flex-token on window focus and on the periodic timer", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false });
    vi.stubGlobal("fetch", fetchMock);
    const { default: FooterTelemetryStrip } = await import(
      "../components/FooterTelemetryStrip"
    );

    render(<FooterTelemetryStrip />);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/flex-token");

    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      vi.advanceTimersByTime(5 * 60_000);
    });
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(
      fetchMock.mock.calls.every((c) => String(c[0]) === "/api/flex-token"),
    ).toBe(true);
  });
});
