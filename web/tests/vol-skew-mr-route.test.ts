import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const mocks = vi.hoisted(() => ({
  radonFetch: vi.fn(),
  getDb: vi.fn(),
}));

vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => mocks.getDb() }));

function dbRow(snapshot: { scan_time: string }) {
  return dbReturning([{ scan_time: snapshot.scan_time, payload: JSON.stringify(snapshot) }]);
}

function dbReturning(rows: Array<{ scan_time: string; payload: string }>) {
  return { execute: async () => ({ rows }) };
}

vi.mock("@/lib/radonApi", () => ({
  radonFetch: mocks.radonFetch,
  RadonApiError: class RadonApiError extends Error {
    status: number;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
    }
  },
}));

const payload = {
  scan_time: "2026-09-16T15:00:00Z",
  source: "Unusual Whales + Radon vol/skew feeds",
  universe: "fallback:ndx100",
  requested_tickers: ["AAPL", "MSFT"],
  tickers_scanned: 2,
  candidates_found: 2,
  actionable_count: 1,
  results: [
    {
      ticker: "AAPL",
      verdict: "TOP_MR",
      spot: 212.4,
      rsi: 78,
      pct_b: 1.04,
      extension: "HIGH",
      iv_path: "falling",
      skew_path: "falling",
      suggested_structure: "put spread",
      gates: { technicals: true, iv: true, skew: true },
      errors: [],
    },
  ],
};

function noStoreHeader(res: Response): string {
  return (res.headers.get("Cache-Control") ?? "").toLowerCase();
}

beforeEach(() => {
  vi.resetModules();
  mocks.radonFetch.mockReset();
  mocks.getDb.mockReset();
  mocks.getDb.mockReturnValue(dbRow(payload));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("GET /api/scanner/vol-skew-mr", () => {
  it("serves the vol/skew MR cache with no-store metadata", async () => {
    const { GET } = await import("../app/api/scanner/vol-skew-mr/route");

    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(noStoreHeader(res)).toContain("no-store");
    expect(body.results[0].ticker).toBe("AAPL");
    expect(body.actionable_count).toBe(1);
    expect(body.cache_meta.last_refresh).toBe("2026-09-16T15:00:00.000Z");
  });

  it("returns the empty envelope when no cache exists", async () => {
    mocks.getDb.mockReturnValue(dbReturning([]));
    const { GET } = await import("../app/api/scanner/vol-skew-mr/route");

    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(noStoreHeader(res)).toContain("no-store");
    expect(body.results).toEqual([]);
    expect(body.cache_meta.is_stale).toBe(true);
  });
});

describe("POST /api/scanner/vol-skew-mr/scan", () => {
  it("runs the FastAPI scan endpoint with preset and limit", async () => {
    mocks.radonFetch.mockResolvedValueOnce(payload);
    const { POST } = await import("../app/api/scanner/vol-skew-mr/scan/route");
    const req = new Request("http://localhost/api/scanner/vol-skew-mr/scan", {
      method: "POST",
      body: JSON.stringify({ preset: "ndx100", limit: 2 }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(noStoreHeader(res)).toContain("no-store");
    expect(body.actionable_count).toBe(1);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/vol-skew-mr/scan?preset=ndx100&limit=2",
      { method: "POST", timeout: 490_000 },
    );
  });

  it("runs the FastAPI scan endpoint for comma tickers", async () => {
    mocks.radonFetch.mockResolvedValueOnce({ ...payload, universe: "explicit", requested_tickers: ["AAPL", "MSFT"] });
    const { POST } = await import("../app/api/scanner/vol-skew-mr/scan/route");
    const req = new Request("http://localhost/api/scanner/vol-skew-mr/scan", {
      method: "POST",
      body: JSON.stringify({ tickers: ["aapl", "msft"] }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.requested_tickers).toEqual(["AAPL", "MSFT"]);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/vol-skew-mr/scan?tickers=AAPL%2CMSFT",
      { method: "POST", timeout: 490_000 },
    );
  });

  it("rejects malformed ticker searches before hitting FastAPI", async () => {
    const { POST } = await import("../app/api/scanner/vol-skew-mr/scan/route");
    const req = new Request("http://localhost/api/scanner/vol-skew-mr/scan", {
      method: "POST",
      body: JSON.stringify({ ticker: "MU1" }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(400);
    expect(body.error).toContain("1-6 letter");
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });
});
