/**
 * @vitest-environment jsdom
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useServiceHealth } from "../lib/useServiceHealth";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useServiceHealth failure state", () => {
  it("finishes loading and exposes a non-2xx response as an error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("down", { status: 503 })));

    const { result } = renderHook(() => useServiceHealth());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBeNull();
    expect(result.current.error).toContain("503");
  });
});
