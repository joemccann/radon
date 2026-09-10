/**
 * Contract test for the per-ticker flow analysis route.
 *
 * Mocks fs and `radonFetch` so we can exercise the GET / POST behaviour
 * without hitting disk or FastAPI.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("fs/promises", () => ({
  readFile: vi.fn(),
}));

vi.mock("fs", () => ({
  statSync: vi.fn(() => ({ mtime: new Date("2026-05-08T12:00:00Z") })),
}));

vi.mock("@/lib/radonApi", () => ({
  radonFetch: vi.fn(),
}));

import { readFile } from "fs/promises";
import { radonFetch } from "@/lib/radonApi";
import { GET, POST } from "@/app/api/flow-analysis/[ticker]/route";

const mockReadFile = readFile as unknown as ReturnType<typeof vi.fn>;
const mockRadonFetch = radonFetch as unknown as ReturnType<typeof vi.fn>;

function makeRequest(): Request {
  return new Request("http://localhost/api/flow-analysis/AAPL");
}

function ctx(ticker: string) {
  return { params: Promise.resolve({ ticker }) };
}

describe("/api/flow-analysis/[ticker]", () => {
  beforeEach(() => {
    mockReadFile.mockReset();
    mockRadonFetch.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe("ticker validation", () => {
    it("400 when symbol contains digits", async () => {
      const res = await GET(makeRequest(), ctx("BRK1"));
      expect(res.status).toBe(400);
      const body = await res.json();
      expect(body.error).toMatch(/Invalid/);
    });

    it("400 on empty / overlong", async () => {
      const res = await GET(makeRequest(), ctx("AAPLAA"));
      expect(res.status).toBe(400);
    });

    it("uppercases mixed case before lookup", async () => {
      mockReadFile.mockResolvedValue(JSON.stringify({ ticker: "AAPL" }));
      const res = await GET(makeRequest(), ctx("aApl"));
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.ticker).toBe("AAPL");
    });
  });

  describe("GET", () => {
    // Regression: previously 404'd, which surfaced as a noisy red error in the
    // browser console even though it's a legitimate first-time-scan signal.
    // The route now returns 200 + { missing: true } so the hook can branch
    // without the network panel screaming.
    it("200 + missing:true when no cache file exists (no console-noisy 404)", async () => {
      mockReadFile.mockRejectedValue(new Error("ENOENT"));
      const res = await GET(makeRequest(), ctx("EWY"));
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.missing).toBe(true);
      expect(body.ticker).toBe("EWY");
      expect(body.cache_meta).toBeTruthy();
    });

    it("200 + cache_meta when cache hit", async () => {
      mockReadFile.mockResolvedValue(
        JSON.stringify({
          ticker: "AAPL",
          fetched_at: "2026-05-08T12:00:00Z",
          verdict: { direction: "BULLISH", confidence: 80 },
        }),
      );
      const res = await GET(makeRequest(), ctx("AAPL"));
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.ticker).toBe("AAPL");
      expect(body.verdict.direction).toBe("BULLISH");
      expect(body.cache_meta).toBeTruthy();
      expect(typeof body.cache_meta.age_seconds).toBe("number");
    });
  });

  describe("POST", () => {
    it("dispatches to radonFetch and returns the report", async () => {
      mockRadonFetch.mockResolvedValue({
        ticker: "AAPL",
        verdict: { direction: "BEARISH", confidence: 65 },
      });
      const res = await POST(makeRequest(), ctx("AAPL"));
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.verdict.direction).toBe("BEARISH");
      expect(mockRadonFetch).toHaveBeenCalledWith(
        "/flow-analysis/AAPL",
        expect.objectContaining({ method: "POST" }),
      );
    });

    it("falls back to cached report when FastAPI fails", async () => {
      mockRadonFetch.mockRejectedValue(new Error("FastAPI down"));
      mockReadFile.mockResolvedValue(
        JSON.stringify({ ticker: "AAPL", verdict: { direction: "NEUTRAL", confidence: 0 } }),
      );
      const res = await POST(makeRequest(), ctx("AAPL"));
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.is_stale).toBe(true);
      expect(res.headers.get("X-Sync-Warning")).toContain("cached");
    });

    it("502 when FastAPI fails and no cache exists", async () => {
      mockRadonFetch.mockRejectedValue(new Error("FastAPI down"));
      mockReadFile.mockRejectedValue(new Error("ENOENT"));
      const res = await POST(makeRequest(), ctx("AAPL"));
      expect(res.status).toBe(502);
    });

    /* R-464 / REL-164: FastAPI detaches the scan from this request (8db918a5),
     * so the 25s client timeout is the DESIGN path, not a failure. It exited
     * through the catch above and told the operator the API was unavailable
     * while `_scan_and_cache` was still running. */
    describe("the 25s timeout is a scan still running, not an outage", () => {
      const timeout = () => new DOMException("The operation was aborted due to timeout", "TimeoutError");

      it("202 scan_pending with the cached report and no API-unavailable warning", async () => {
        mockRadonFetch.mockRejectedValue(timeout());
        mockReadFile.mockResolvedValue(
          JSON.stringify({ ticker: "AAPL", verdict: { direction: "NEUTRAL", confidence: 0 } }),
        );
        const res = await POST(makeRequest(), ctx("AAPL"));
        expect(res.status).toBe(202);
        const body = await res.json();
        expect(body.scan_pending).toBe(true);
        expect(body.is_stale).toBeUndefined();
        expect(body.verdict.direction).toBe("NEUTRAL");
        expect(body.cache_meta.last_refresh).toBe("2026-05-08T12:00:00.000Z");
        expect(res.headers.get("X-Sync-Warning")).toBeNull();
      });

      it("202 scan_pending with no report when this is the ticker's first scan", async () => {
        mockRadonFetch.mockRejectedValue(timeout());
        mockReadFile.mockRejectedValue(new Error("ENOENT"));
        const res = await POST(makeRequest(), ctx("AAPL"));
        expect(res.status).toBe(202);
        const body = await res.json();
        expect(body).toMatchObject({ ticker: "AAPL", scan_pending: true });
        expect(body.verdict).toBeUndefined();
        expect(body.cache_meta).toBeTruthy();
      });
    });
  });
});
