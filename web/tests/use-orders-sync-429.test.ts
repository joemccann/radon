// @vitest-environment jsdom
/**
 * Same contract as use-portfolio-sync-429: a 429 on POST /api/orders
 * (`orders-refresh`, 4/min per user) is a sibling sync that already ran,
 * not an error to paint on the sync pill.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import { useOrders } from "../lib/useOrders";

const orders = { orders: [], last_sync: "2026-08-24T14:00:00.000Z" };

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

describe("useOrders sync POST rate-limited", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return jsonResponse({ error: "Too Many Requests" }, { status: 429, headers: { "Retry-After": "20" } });
      }
      return jsonResponse(orders);
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("keeps the snapshot and reports no error when the sync POST is 429", async () => {
    const { result } = renderHook(() => useOrders(true));
    await waitFor(() => expect(result.current.data).not.toBeNull());

    await act(async () => {
      result.current.syncNow();
    });
    await waitFor(() => expect(result.current.syncing).toBe(false));

    expect(result.current.error).toBeNull();
    expect(result.current.data?.last_sync).toBe(orders.last_sync);
  });
});
