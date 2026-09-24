/** @vitest-environment jsdom */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";

const mocks = vi.hoisted(() => ({
  indexHook: vi.fn(),
}));

vi.mock("@/lib/TickerDetailContext", () => ({ useTickerDetailOptional: () => null }));
vi.mock("@/lib/useIndexOptionsChain", () => ({
  useIndexOptionsChain: (symbol: string | null, expiry: string | null) =>
    mocks.indexHook(symbol, expiry),
}));

import { IndexOptionOrderForm } from "../components/ticker-detail/IndexOptionOrderForm";

afterEach(() => {
  cleanup();
  mocks.indexHook.mockReset();
  vi.unstubAllGlobals();
});

describe("IndexOptionOrderForm expirations", () => {
  it("loads expirations from the equity route and never requests an unscoped index chain", async () => {
    const fetchMock = vi.fn(async (url: string) => ({
      ok: true,
      json: async () => ({ symbol: "VIX", expirations: ["20261020"] }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    mocks.indexHook.mockImplementation((symbol: string | null, expiry: string | null) => ({
      data:
        symbol && expiry
          ? {
              symbol,
              exchange: "CBOE",
              tradingClass: "VIX",
              expirations: [expiry],
              contracts: [],
              count: 0,
            }
          : null,
      loading: false,
      error: null,
    }));

    render(<IndexOptionOrderForm ticker="vix" portfolio={null} />);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/options/expirations?symbol=VIX", {
        cache: "no-store",
      });
    });
    await waitFor(() => {
      expect(mocks.indexHook).toHaveBeenCalledWith("VIX", "20261020");
    });
    expect(mocks.indexHook).not.toHaveBeenCalledWith("VIX", null);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
