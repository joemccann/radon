/**
 * Unit tests: useShortAvailability hook logic + GET /api/short-availability/[ticker]
 * proxy route.
 *
 * Tests for `LocateFeeChip` rendering live in short-availability-chip.test.tsx.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { resolveLocateChipEnabled } from "@/lib/order/risk/OrderRiskGate";
import type { PortfolioData } from "@/lib/types";
import type { OrderRiskState } from "@/lib/order/risk";

// ---------------------------------------------------------------------------
// Mock radonFetch for Next.js route tests
// ---------------------------------------------------------------------------

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
  RadonApiError: class RadonApiError extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(`Radon API ${status}: ${detail}`);
      this.name = "RadonApiError";
      this.status = status;
      this.detail = detail;
    }
  },
}));

// Mock apiContracts helpers (avoid Web Crypto in test env)
vi.mock("@/lib/apiContracts", () => ({
  getRequestId: () => "test-rid-123",
  setNoStoreResponseHeaders: (response: Response) => response,
}));

// ---------------------------------------------------------------------------
// Shared fixture factory
// ---------------------------------------------------------------------------

function makeShortData(overrides: Record<string, unknown> = {}) {
  return {
    ticker: "SPY",
    shortable: true,
    difficulty: 3.0,
    shortable_shares: 1_500_000,
    fee_rate: 0.25,
    rebate_rate: 0.10,
    source: "ib",
    as_of: "2026-06-12T14:00:00Z",
    missing: false,
    ...overrides,
  };
}

// Helper: build the params object the updated route expects
function makeCtx(ticker: string) {
  return { params: Promise.resolve({ ticker }) };
}

// ---------------------------------------------------------------------------
// Route tests
// ---------------------------------------------------------------------------

describe("GET /api/short-availability/[ticker]", () => {
  beforeEach(() => {
    vi.resetModules();
    mockRadonFetch.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("proxies happy-path IB response as-is", async () => {
    const payload = makeShortData();
    mockRadonFetch.mockResolvedValueOnce(payload);

    const { GET } = await import(
      "@/app/api/short-availability/[ticker]/route"
    );
    const response = await GET(new Request("http://localhost"), makeCtx("SPY"));

    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({ ticker: "SPY", shortable: true, missing: false });
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/short-availability/SPY",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("uppercases lowercase ticker before calling FastAPI", async () => {
    mockRadonFetch.mockResolvedValueOnce(makeShortData({ ticker: "spy" }));
    const { GET } = await import(
      "@/app/api/short-availability/[ticker]/route"
    );
    await GET(new Request("http://localhost"), makeCtx("spy"));
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/short-availability/SPY",
      expect.anything(),
    );
  });

  it("returns missing:true payload (200) when FastAPI throws any error", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("ECONNREFUSED"));

    const { GET } = await import(
      "@/app/api/short-availability/[ticker]/route"
    );
    const response = await GET(new Request("http://localhost"), makeCtx("TSLA"));

    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.missing).toBe(true);
    expect(body.ticker).toBe("TSLA");
    expect(body.source).toBe("none");
  });

  it("returns missing:true for an invalid ticker pattern (digits only)", async () => {
    const { GET } = await import(
      "@/app/api/short-availability/[ticker]/route"
    );
    const response = await GET(new Request("http://localhost"), makeCtx("12345"));

    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.missing).toBe(true);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("passes not-shortable response through unchanged", async () => {
    const payload = makeShortData({ shortable: false, difficulty: 1.2, missing: false });
    mockRadonFetch.mockResolvedValueOnce(payload);

    const { GET } = await import(
      "@/app/api/short-availability/[ticker]/route"
    );
    const response = await GET(new Request("http://localhost"), makeCtx("GME"));
    const body = await response.json();
    expect(body.shortable).toBe(false);
    expect(body.difficulty).toBe(1.2);
  });

  it("passes HTB (hard-to-borrow) UW data through", async () => {
    const payload = makeShortData({
      shortable: null,
      difficulty: 2.0,
      fee_rate: 12.5,
      source: "uw",
    });
    mockRadonFetch.mockResolvedValueOnce(payload);

    const { GET } = await import(
      "@/app/api/short-availability/[ticker]/route"
    );
    const response = await GET(new Request("http://localhost"), makeCtx("SPCE"));
    const body = await response.json();
    expect(body.fee_rate).toBe(12.5);
    expect(body.source).toBe("uw");
  });

  it("returns missing:true payload for empty ticker string", async () => {
    const { GET } = await import(
      "@/app/api/short-availability/[ticker]/route"
    );
    // Empty string fails the TICKER_RE test
    const response = await GET(new Request("http://localhost"), makeCtx(""));
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.missing).toBe(true);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// OrderRiskGate locate-chip enablement logic (pure function tests)
// ---------------------------------------------------------------------------

// We test the resolveLocateChipEnabled logic by importing the gate file and
// checking the exported helpers. Since they are not exported, we test the
// observable behaviour through the chip logic contracts.

describe("Locate chip enablement logic", () => {
  const sellCall = {
    ticker: "SPY",
    chainLegs: [{ action: "SELL" as const, right: "C" as const, strike: 500, expiry: "20261219", quantity: 1 }],
    netPremium: -1,
    description: "Short Call",
    totalCost: -100,
  };
  const portfolio = { positions: [{ ticker: "SPY", legs: [{ type: "Put" }] }] } as unknown as PortfolioData;

  function state(undefinedRiskReason: string | null): OrderRiskState {
    return {
      coverageStatus: "resolved",
      okToSubmit: true,
      coveringLegs: [],
      summary: { undefinedRiskReason },
    } as unknown as OrderRiskState;
  }

  it("an unrelated same-ticker option does not suppress a naked-short locate", () => {
    expect(resolveLocateChipEnabled(
      sellCall,
      portfolio,
      state("Uncovered short call"),
    )).toBe(true);
  });

  it("resolved structural coverage suppresses the locate", () => {
    expect(resolveLocateChipEnabled(sellCall, portfolio, state(null))).toBe(false);
  });

  it("pending or missing portfolio coverage cannot claim a resolved locate state", () => {
    expect(resolveLocateChipEnabled(sellCall, undefined, null)).toBe(false);
    expect(resolveLocateChipEnabled(sellCall, null, null)).toBe(false);
  });

  it("an order with only BUY legs never enables the chip", () => {
    const buyCall = {
      ...sellCall,
      chainLegs: [{ ...sellCall.chainLegs[0], action: "BUY" as const }],
      netPremium: 1,
      totalCost: 100,
      description: "Long Call",
    };
    expect(resolveLocateChipEnabled(buyCall, portfolio, state("Uncovered short call"))).toBe(false);
  });

  it("a multi-leg order with one SELL leg is evaluated for the chip", () => {
    const callSpread = {
      ...sellCall,
      chainLegs: [
        { ...sellCall.chainLegs[0], action: "BUY" as const },
        { ...sellCall.chainLegs[0], strike: 510 },
      ],
      description: "Bull Call Spread",
    };
    expect(resolveLocateChipEnabled(callSpread, portfolio, state("Uncovered short call"))).toBe(true);
  });
});
