/** @vitest-environment jsdom */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useCatalysts } from "@/lib/useCatalysts";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useCatalysts", () => {
  it("refreshes immediately when a backgrounded dashboard becomes visible", async () => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        scan_time: "2026-08-07T10:30:00Z",
        count: 0,
        catalysts: [],
      })))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        scan_time: "2026-08-07T20:00:00Z",
        count: 0,
        catalysts: [],
      })));
    global.fetch = fetchMock as typeof fetch;

    const { result } = renderHook(() => useCatalysts(true));
    await waitFor(() => expect(result.current.data?.scan_time).toBe("2026-08-07T10:30:00Z"));

    act(() => document.dispatchEvent(new Event("visibilitychange")));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.data?.scan_time).toBe("2026-08-07T20:00:00Z"));
  });
});
