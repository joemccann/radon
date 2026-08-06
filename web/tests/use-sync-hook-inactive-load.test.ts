/**
 * @vitest-environment jsdom
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useSyncHook } from "../lib/useSyncHook";

type Payload = {
  scan_time: string;
  value: number;
};

function jsonResponse(body: Payload) {
  return {
    ok: true,
    async json() {
      return body;
    },
  };
}

describe("useSyncHook inactive initial load", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads cached data once even when inactive", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ scan_time: "2026-03-22T09:00:00Z", value: 7 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useSyncHook<Payload>(
        {
          endpoint: "/api/internals",
          hasPost: false,
          extractTimestamp: (data) => data.scan_time,
        },
        false,
      ),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.data?.value).toBe(7);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/internals", { method: "GET", cache: "no-store" });
  });

  it("triggers the first sync when a previously inactive hook becomes active", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ scan_time: "2026-03-22T09:00:00Z", value: 7 }))
      .mockResolvedValueOnce(jsonResponse({ scan_time: "2026-03-22T09:01:00Z", value: 8 }));
    vi.stubGlobal("fetch", fetchMock);

    const { result, rerender } = renderHook(
      ({ active }: { active: boolean }) =>
        useSyncHook<Payload>(
          {
            endpoint: "/api/internals",
            extractTimestamp: (data) => data.scan_time,
          },
          active,
        ),
      { initialProps: { active: false } },
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data?.value).toBe(7);

    rerender({ active: true });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/internals", { method: "POST", cache: "no-store" });
  });

  it("skips mount GET when loadWhenInactive is false and inactive", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ scan_time: "2026-03-22T09:00:00Z", value: 7 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result, rerender } = renderHook(
      ({ active }: { active: boolean }) =>
        useSyncHook<Payload>(
          {
            endpoint: "/api/scanner/theta",
            hasPost: false,
            loadWhenInactive: false,
            extractTimestamp: (data) => data.scan_time,
          },
          active,
        ),
      { initialProps: { active: false } },
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();

    rerender({ active: true });

    await waitFor(() => expect(result.current.data?.value).toBe(7));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/scanner/theta", {
      method: "GET",
      cache: "no-store",
    });
  });
});
