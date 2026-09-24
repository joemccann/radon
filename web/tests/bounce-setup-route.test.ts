/**
 * @vitest-environment node
 *
 * /api/scanner/bounce (GET cache) and /api/scanner/bounce/scan (POST, spawns
 * the FastAPI scan). docs/bounce-setup.md.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const mocks = vi.hoisted(() => ({
  readFile: vi.fn(),
  radonFetch: vi.fn(),
  getDb: vi.fn(),
}));

vi.mock("fs/promises", () => ({ readFile: mocks.readFile }));
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => mocks.getDb() }));
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

function dbReturning(rows: Array<{ scan_time: string; payload: string }>) {
  return { execute: async () => ({ rows }) };
}

const payload = {
  scan_time: new Date().toISOString(),
  as_of: "2026-09-18",
  window: 20,
  universe: "largecaps",
  coverage: { tickers: 520, ranked: 512, excluded_short_history: 8, stage2: 30 },
  bounce_count: 1,
  results: [
    {
      ticker: "BAC",
      verdict: "BOUNCE_SETUP",
      stretch_rank: 1,
      stretch_pctl: 0.2,
      rsi: 24.1,
      pct_b: -0.12,
      ret_z: -2.4,
      ret_20d: -5.5,
      contract: { symbol: "BAC261016P00055000", expiry: "2026-10-16", strike: 55 },
      vol: { runup: 4.5, off_peak: 0.8, slope: -1.9, pass: true },
      skew: { ease: 0.5, slope: -0.4, pass: true },
      series: [],
      flow: null,
      errors: [],
    },
  ],
};

const ENOENT = Object.assign(new Error("ENOENT"), { code: "ENOENT" });
const noStore = (res: Response) => (res.headers.get("Cache-Control") ?? "").toLowerCase();

beforeEach(() => {
  vi.resetModules();
  mocks.readFile.mockReset().mockRejectedValue(ENOENT);
  mocks.radonFetch.mockReset();
  mocks.getDb.mockReset().mockReturnValue(dbReturning([]));
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /api/scanner/bounce", () => {
  it("serves the latest bounce-setup snapshot from Turso", async () => {
    mocks.getDb.mockReturnValue(dbReturning([{ scan_time: payload.scan_time, payload: JSON.stringify(payload) }]));
    const { GET } = await import("../app/api/scanner/bounce/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.results[0].ticker).toBe("BAC");
    expect(body.bounce_count).toBe(1);
  });

  it("falls back to data/bounce_setup.json", async () => {
    mocks.readFile.mockResolvedValue(JSON.stringify(payload));
    const { GET } = await import("../app/api/scanner/bounce/route");
    const body = await (await GET()).json();
    expect(body.results[0].verdict).toBe("BOUNCE_SETUP");
    expect(String(mocks.readFile.mock.calls[0][0])).toMatch(/data[\\/]bounce_setup\.json$/);
  });

  it("returns the missing contract at HTTP 200 with no-store", async () => {
    const { GET } = await import("../app/api/scanner/bounce/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(noStore(res)).toContain("no-store");
    const body = await res.json();
    expect(body).toMatchObject({ missing: true, scan_time: null, results: [], bounce_count: 0 });
  });

  it("declares force-dynamic, nodejs and the read capability", async () => {
    const route = await import("../app/api/scanner/bounce/route");
    expect(route.dynamic).toBe("force-dynamic");
    expect(route.runtime).toBe("nodejs");
    expect(route.radonCapability).toBe("read");
  });
});

describe("POST /api/scanner/bounce/scan", () => {
  it("defaults to the largecaps universe on the wire", async () => {
    mocks.radonFetch.mockResolvedValueOnce(payload);
    const { POST } = await import("../app/api/scanner/bounce/scan/route");
    const res = await POST(new Request("http://localhost/api/scanner/bounce/scan", { method: "POST" }));
    expect(res.status).toBe(200);
    expect(mocks.radonFetch).toHaveBeenCalledTimes(1);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/bounce-setup/scan?preset=largecaps",
      { method: "POST", timeout: 490_000 },
    );
  });

  it("passes explicit tickers instead of the preset", async () => {
    mocks.radonFetch.mockResolvedValueOnce(payload);
    const { POST } = await import("../app/api/scanner/bounce/scan/route");
    await POST(new Request("http://localhost/api/scanner/bounce/scan", {
      method: "POST",
      body: JSON.stringify({ tickers: ["bac", "wfc"] }),
    }));
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/bounce-setup/scan?tickers=BAC%2CWFC",
      { method: "POST", timeout: 490_000 },
    );
  });

  it("is pinned read.spawn", async () => {
    const route = await import("../app/api/scanner/bounce/scan/route");
    expect(route.radonCapability).toBe("read.spawn");
  });

  it("declares maxDuration = 600", async () => {
    const route = await import("../app/api/scanner/bounce/scan/route");
    expect(route.maxDuration).toBe(600);
  });

  it("falls back to cached bounce payload on >=500 API errors when cache matches request", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mocks.radonFetch.mockRejectedValueOnce(new RadonApiError(504, "Gateway Timeout"));
    mocks.readFile.mockResolvedValueOnce(JSON.stringify(payload));
    const { POST } = await import("../app/api/scanner/bounce/scan/route");
    const res = await POST(new Request("http://localhost/api/scanner/bounce/scan", { method: "POST" }));
    expect(res.status).toBe(504);
    expect(res.headers.get("X-Sync-Warning")).toBe(
      "Radon API unavailable - matching cached bounce setup attached",
    );
    const body = await res.json();
    expect(body.is_stale).toBe(true);
    expect(body.scan_succeeded).toBe(false);
    expect(body.results[0].ticker).toBe("BAC");
  });

  it("returns missing payload with error when upstream fails and no matching cache exists", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mocks.radonFetch.mockRejectedValueOnce(new RadonApiError(502, "Bad Gateway"));
    mocks.readFile.mockRejectedValueOnce(ENOENT);
    const { POST } = await import("../app/api/scanner/bounce/scan/route");
    const res = await POST(new Request("http://localhost/api/scanner/bounce/scan", { method: "POST" }));
    expect(res.status).toBe(502);
    expect(res.headers.get("X-Sync-Warning")).toBeNull();
    const body = await res.json();
    expect(body.missing).toBe(true);
    expect(body.scan_succeeded).toBe(false);
    expect(body.error).toBe("Bad Gateway");
  });
});
