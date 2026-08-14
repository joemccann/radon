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

/** A stub libsql client whose theta snapshot query returns `rows`. */
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

const thetaPayload = {
  scan_time: "2026-06-24T15:00:00Z",
  source: "Unusual Whales",
  universe: "fallback:ndx100",
  requested_tickers: ["AAPL", "MSFT"],
  tickers_scanned: 2,
  candidates_found: 1,
  theta_harvest_count: 1,
  results: [
    {
      ticker: "AAPL",
      verdict: "THETA_HARVEST",
      score: 87.5,
      spot: 100,
      structure: {
        expiry: "20260717",
        dte: 23,
        net_delta: -0.01,
        theta: 0.075,
        gamma: -0.0042,
        vega: -0.038,
        credit: 1.9,
        short_put: { strike: 95, right: "P" },
        short_call: { strike: 105, right: "C" },
      },
      gates: {
        delta_near_zero: true,
        iv_rich_vs_rv: true,
        dealer_support: true,
        theta_positive: true,
      },
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
  mocks.readFile.mockResolvedValue(JSON.stringify(thetaPayload));
  // Default: no Turso row → GET falls back to the disk cache.
  mocks.getDb.mockReturnValue(dbReturning([]));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("GET /api/scanner/theta", () => {
  it("serves the theta cache with no-store metadata", async () => {
    const { GET } = await import("../app/api/scanner/theta/route");

    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(noStoreHeader(res)).toContain("no-store");
    expect(body.results[0].ticker).toBe("AAPL");
    expect(body.cache_meta.last_refresh).toBe("2026-06-24T15:01:00.000Z");
  });

  it("returns the empty envelope when no cache exists", async () => {
    mocks.readFile.mockRejectedValueOnce(new Error("ENOENT"));
    mocks.statSync.mockImplementationOnce(() => {
      throw new Error("ENOENT");
    });
    const { GET } = await import("../app/api/scanner/theta/route");

    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(noStoreHeader(res)).toContain("no-store");
    expect(body.results).toEqual([]);
    expect(body.cache_meta.is_stale).toBe(true);
  });

  it("serves the last populated Turso snapshot when a later quota scan wrote zero rows", async () => {
    mocks.readFile.mockResolvedValue(
      JSON.stringify({
        ...thetaPayload,
        scan_time: "2026-08-14T17:15:18.159060+00:00",
        candidates_found: 0,
        theta_harvest_count: 0,
        tickers_scanned: 102,
        results: [],
      }),
    );
    mocks.getDb.mockReturnValue(
      dbReturning([
        {
          scan_time: "2026-08-14T17:15:18.159060+00:00",
          payload: JSON.stringify({
            ...thetaPayload,
            scan_time: "2026-08-14T17:15:18.159060+00:00",
            candidates_found: 0,
            theta_harvest_count: 0,
            tickers_scanned: 102,
            results: [],
          }),
        },
        {
          scan_time: "2026-08-14T15:30:09.987292+00:00",
          payload: JSON.stringify({
            ...thetaPayload,
            scan_time: "2026-08-14T15:30:09.987292+00:00",
            candidates_found: 59,
            theta_harvest_count: 28,
            tickers_scanned: 102,
            results: [{ ...thetaPayload.results[0], ticker: "TXN" }],
          }),
        },
      ]),
    );
    const { GET } = await import("../app/api/scanner/theta/route");

    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.results[0].ticker).toBe("TXN");
    expect(body.candidates_found).toBe(59);
    expect(body.scan_time).toBe("2026-08-14T15:30:09.987292+00:00");
  });

  it("prefers the fresher Turso snapshot over a staler host-local disk cache", async () => {
    // Disk has the old NDX scan (AAPL @ 2026-06-24); Turso carries a newer
    // single-ticker scan (NVDA @ 2026-06-25). This is the prod split-brain:
    // the scan ran on another host and is only visible via the shared DB.
    mocks.getDb.mockReturnValue(
      dbReturning([
        {
          scan_time: "2026-06-25T15:00:00Z",
          payload: JSON.stringify({
            ...thetaPayload,
            scan_time: "2026-06-25T15:00:00Z",
            universe: "explicit",
            requested_tickers: ["NVDA"],
            results: [{ ...thetaPayload.results[0], ticker: "NVDA" }],
          }),
        },
      ]),
    );
    const { GET } = await import("../app/api/scanner/theta/route");

    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.results[0].ticker).toBe("NVDA");
    expect(body.universe).toBe("explicit");
  });

  it("backfills earnings on pre-feature snapshots that omit the earnings key", async () => {
    // Snapshot shape before earnings annotation shipped — no `earnings` key.
    mocks.readFile.mockResolvedValueOnce(
      JSON.stringify({
        ...thetaPayload,
        results: [
          {
            ticker: "HONA",
            verdict: "THETA_HARVEST",
            score: 98.4,
            structure: {
              expiry: "20260821",
              dte: 16,
              net_delta: -0.005,
              theta: 0.4127,
              short_put: { strike: 190, right: "P" },
              short_call: { strike: 240, right: "C" },
            },
          },
        ],
      }),
    );
    mocks.radonFetch.mockResolvedValueOnce({
      results: [
        {
          ticker: "HONA",
          report_date: "2026-08-05",
          report_time: "postmarket",
          days_until: 0,
          source: "company",
          expected_move_pct: 8.82,
          missing: false,
        },
      ],
      count: 1,
    });

    const { GET } = await import("../app/api/scanner/theta/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.results[0].ticker).toBe("HONA");
    expect(body.results[0].earnings).toMatchObject({
      report_date: "2026-08-05",
      report_time: "postmarket",
      days_until: 0,
      within_dte: true,
    });
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/earnings?tickers=HONA",
      { timeout: 90_000 },
    );
  });

  it("does not call earnings when the snapshot already has the earnings key", async () => {
    mocks.readFile.mockResolvedValueOnce(
      JSON.stringify({
        ...thetaPayload,
        results: [{ ...thetaPayload.results[0], earnings: null }],
      }),
    );
    const { GET } = await import("../app/api/scanner/theta/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.results[0].earnings).toBeNull();
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });
});

describe("POST /api/scanner/theta/scan", () => {
  it("runs the FastAPI scan endpoint with preset and limit", async () => {
    mocks.radonFetch.mockResolvedValueOnce(thetaPayload);
    const { POST } = await import("../app/api/scanner/theta/scan/route");
    const req = new Request("http://localhost/api/scanner/theta/scan", {
      method: "POST",
      body: JSON.stringify({ preset: "ndx100", limit: 2 }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(noStoreHeader(res)).toContain("no-store");
    expect(body.theta_harvest_count).toBe(1);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/theta-harvester/scan?preset=ndx100&limit=2",
      { method: "POST", timeout: 430_000 },
    );
  });

  it("runs the FastAPI scan endpoint for a specific ticker", async () => {
    mocks.radonFetch.mockResolvedValueOnce({ ...thetaPayload, universe: "explicit", requested_tickers: ["MU"] });
    const { POST } = await import("../app/api/scanner/theta/scan/route");
    const req = new Request("http://localhost/api/scanner/theta/scan", {
      method: "POST",
      body: JSON.stringify({ ticker: "mu" }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.requested_tickers).toEqual(["MU"]);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/theta-harvester/scan?ticker=MU",
      { method: "POST", timeout: 430_000 },
    );
  });

  it("rejects malformed ticker searches before hitting FastAPI", async () => {
    const { POST } = await import("../app/api/scanner/theta/scan/route");
    const req = new Request("http://localhost/api/scanner/theta/scan", {
      method: "POST",
      body: JSON.stringify({ ticker: "MU1" }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(400);
    expect(body.error).toContain("Ticker must be 1-6 letters");
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("failed scans preserve failure status even when matching cache exists", async () => {
    mocks.radonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { POST } = await import("../app/api/scanner/theta/scan/route");
    const req = new Request("http://localhost/api/scanner/theta/scan", {
      method: "POST",
      body: JSON.stringify({ preset: "ndx100" }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(502);
    expect(res.headers.get("X-Sync-Warning")).toContain("matching cached");
    expect(noStoreHeader(res)).toContain("no-store");
    expect(body.is_stale).toBe(true);
    expect(body.scan_succeeded).toBe(false);
    expect(body.results[0].ticker).toBe("AAPL");
  });

  it("returns a 502 empty envelope when both FastAPI and cache fail", async () => {
    mocks.radonFetch.mockRejectedValueOnce(new Error("upstream down"));
    mocks.readFile.mockRejectedValueOnce(new Error("ENOENT"));
    const { POST } = await import("../app/api/scanner/theta/scan/route");
    const req = new Request("http://localhost/api/scanner/theta/scan", {
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
