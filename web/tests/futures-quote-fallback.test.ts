/**
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

const mockFetch = vi.fn();
const mockGlobexOpen = vi.fn(() => true);

vi.mock("@/lib/futuresSession", () => ({
  useGlobexOpen: () => mockGlobexOpen(),
}));

function yahooPayload(symbol: string, price: number, prevClose: number) {
  return {
    chart: {
      result: [{
        meta: {
          symbol,
          regularMarketPrice: price,
          previousClose: prevClose,
          regularMarketTime: 1782239566,
        },
        timestamp: [1782221400, 1782239566],
        indicators: {
          quote: [{ close: [price - 1, price] }],
        },
      }],
    },
  };
}

beforeEach(() => {
  mockFetch.mockReset();
  mockGlobexOpen.mockReset();
  mockGlobexOpen.mockReturnValue(true);
  vi.stubGlobal("fetch", mockFetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GET /api/futures-quote", () => {
  it("maps ES to the Yahoo ES=F continuous-front symbol", async () => {
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify(yahooPayload("ES=F", 5432.5, 5400.25)), { status: 200 }));
    const { GET } = await import("../app/api/futures-quote/route");

    const res = await GET(new Request("http://localhost/api/futures-quote?symbol=ES"));
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.source).toBe("yahoo");
    expect(body.price).toMatchObject({ symbol: "ES", last: 5432.5, close: 5400.25 });
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("ES%3DF"),
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it.each([
    ["NQ", "NQ%3DF"],
    ["RTY", "RTY%3DF"],
  ])("maps %s to its =F symbol", async (symbol, encoded) => {
    mockFetch.mockResolvedValueOnce(new Response(JSON.stringify(yahooPayload(`${symbol}=F`, 100, 99)), { status: 200 }));
    const { GET } = await import("../app/api/futures-quote/route");

    const res = await GET(new Request(`http://localhost/api/futures-quote?symbol=${symbol}`));
    expect(res.status).toBe(200);
    expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining(encoded), expect.anything());
  });

  it("rejects non-futures symbols", async () => {
    const { GET } = await import("../app/api/futures-quote/route");
    const res = await GET(new Request("http://localhost/api/futures-quote?symbol=AAPL"));
    expect(res.status).toBe(400);
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe("useFuturesQuoteFallback gating", () => {
  it("does not fetch while the Globex session is closed", async () => {
    mockGlobexOpen.mockReturnValue(false);
    const { useFuturesQuoteFallback } = await import("../lib/useFuturesQuoteFallback");

    renderHook(() => useFuturesQuoteFallback(["ES", "NQ", "RTY"]));
    await new Promise((r) => setTimeout(r, 10));
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("fetches the futures-quote route for each missing symbol while open", async () => {
    mockGlobexOpen.mockReturnValue(true);
    mockFetch.mockResolvedValue(new Response(JSON.stringify({ price: { symbol: "ES", last: 5432.5, close: 5400.25 } }), { status: 200 }));
    const { useFuturesQuoteFallback } = await import("../lib/useFuturesQuoteFallback");

    renderHook(() => useFuturesQuoteFallback(["ES"]));
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/futures-quote?symbol=ES"),
        expect.objectContaining({ cache: "no-store" }),
      );
    });
  });
});
