import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const mocks = vi.hoisted(() => ({
  readFile: vi.fn(),
  statSync: vi.fn(),
  radonFetch: vi.fn(),
  getDb: vi.fn(),
}));

vi.mock("fs/promises", () => ({
  readFile: mocks.readFile,
}));

vi.mock("fs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs")>();
  return {
    ...actual,
    statSync: mocks.statSync,
  };
});

vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => mocks.getDb() }));

/** A stub libsql client whose strength snapshot query returns `rows`. */
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

const strengthPayload = {
  scan_time: "2026-06-24T15:00:00Z",
  source: "Unusual Whales + Radon regime caches",
  universe: "fallback:ndx100",
  requested_tickers: ["AAPL", "MSFT"],
  tickers_scanned: 2,
  candidates_found: 1,
  confirmed_strength_count: 1,
  results: [
    {
      ticker: "AAPL",
      verdict: "REAL_STRENGTH_CONFIRMED",
      score: 100,
      groups_passed: 7,
      spot: 212.4,
      factors: [],
      errors: [],
    },
  ],
};

function noStoreHeader(res: Response): string {
  return (res.headers.get("Cache-Control") ?? "").toLowerCase();
}

beforeEach(() => {
  vi.resetModules();
  mocks.readFile.mockReset();
  mocks.statSync.mockReset();
  mocks.radonFetch.mockReset();
  mocks.getDb.mockReset();
  mocks.statSync.mockReturnValue({ mtime: new Date("2026-06-24T15:01:00Z") });
  mocks.readFile.mockResolvedValue(JSON.stringify(strengthPayload));
  // Default: no Turso row → GET falls back to the disk cache.
  mocks.getDb.mockReturnValue(dbReturning([]));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("GET /api/scanner/strength", () => {
  it("serves the strength cache with no-store metadata", async () => {
    const { GET } = await import("../app/api/scanner/strength/route");

    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(noStoreHeader(res)).toContain("no-store");
    expect(body.results[0].ticker).toBe("AAPL");
    expect(body.confirmed_strength_count).toBe(1);
    expect(body.cache_meta.last_refresh).toBe("2026-06-24T15:01:00.000Z");
  });

  it("returns the empty envelope when no cache exists", async () => {
    mocks.readFile.mockRejectedValueOnce(new Error("ENOENT"));
    mocks.statSync.mockImplementationOnce(() => {
      throw new Error("ENOENT");
    });
    const { GET } = await import("../app/api/scanner/strength/route");

    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(noStoreHeader(res)).toContain("no-store");
    expect(body.results).toEqual([]);
    expect(body.cache_meta.is_stale).toBe(true);
  });

  it("prefers the fresher Turso snapshot over a staler host-local disk cache", async () => {
    // Disk has the old NDX scan (AAPL @ 2026-06-24); Turso carries a newer
    // single-ticker scan (NVDA @ 2026-06-25). Prod split-brain: the scan ran on
    // another host and is only visible via the shared DB.
    mocks.getDb.mockReturnValue(
      dbReturning([
        {
          scan_time: "2026-06-25T15:00:00Z",
          payload: JSON.stringify({
            ...strengthPayload,
            scan_time: "2026-06-25T15:00:00Z",
            universe: "explicit",
            requested_tickers: ["NVDA"],
            results: [{ ...strengthPayload.results[0], ticker: "NVDA" }],
          }),
        },
      ]),
    );
    const { GET } = await import("../app/api/scanner/strength/route");

    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.results[0].ticker).toBe("NVDA");
    expect(body.universe).toBe("explicit");
  });
});

describe("POST /api/scanner/strength/scan", () => {
  it("runs the FastAPI scan endpoint with preset and limit", async () => {
    mocks.radonFetch.mockResolvedValueOnce(strengthPayload);
    const { POST } = await import("../app/api/scanner/strength/scan/route");
    const req = new Request("http://localhost/api/scanner/strength/scan", {
      method: "POST",
      body: JSON.stringify({ preset: "ndx100", limit: 2 }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(noStoreHeader(res)).toContain("no-store");
    expect(body.confirmed_strength_count).toBe(1);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/strength-confirmation/scan?preset=ndx100&limit=2",
      { method: "POST", timeout: 490_000 },
    );
  });

  it("runs the FastAPI scan endpoint for a specific ticker", async () => {
    mocks.radonFetch.mockResolvedValueOnce({ ...strengthPayload, universe: "explicit", requested_tickers: ["MU"] });
    const { POST } = await import("../app/api/scanner/strength/scan/route");
    const req = new Request("http://localhost/api/scanner/strength/scan", {
      method: "POST",
      body: JSON.stringify({ ticker: "mu" }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.requested_tickers).toEqual(["MU"]);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/strength-confirmation/scan?ticker=MU",
      { method: "POST", timeout: 490_000 },
    );
  });

  it("rejects malformed ticker searches before hitting FastAPI", async () => {
    const { POST } = await import("../app/api/scanner/strength/scan/route");
    const req = new Request("http://localhost/api/scanner/strength/scan", {
      method: "POST",
      body: JSON.stringify({ ticker: "MU1" }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(400);
    expect(body.error).toContain("Ticker must be 1-6 letters");
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("serves stale cache with a sync warning when FastAPI is unavailable", async () => {
    mocks.radonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { POST } = await import("../app/api/scanner/strength/scan/route");
    const req = new Request("http://localhost/api/scanner/strength/scan", {
      method: "POST",
      body: JSON.stringify({ preset: "ndx100" }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(res.headers.get("X-Sync-Warning")).toContain("serving cached");
    expect(noStoreHeader(res)).toContain("no-store");
    expect(body.is_stale).toBe(true);
    expect(body.results[0].ticker).toBe("AAPL");
  });

  it("returns a 502 empty envelope when both FastAPI and cache fail", async () => {
    mocks.radonFetch.mockRejectedValueOnce(new Error("upstream down"));
    mocks.readFile.mockRejectedValueOnce(new Error("ENOENT"));
    const { POST } = await import("../app/api/scanner/strength/scan/route");
    const req = new Request("http://localhost/api/scanner/strength/scan", {
      method: "POST",
      body: JSON.stringify({}),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(502);
    expect(noStoreHeader(res)).toContain("no-store");
    expect(body.results).toEqual([]);
    expect(body.error).toContain("upstream down");
  });
});
