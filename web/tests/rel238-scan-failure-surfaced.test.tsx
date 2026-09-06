/**
 * @vitest-environment jsdom
 *
 * REL-238 (R-643 + R-660): scan-trigger POST routes must not swallow an
 * upstream failure into HTTP 200 + cached body signalled only by an
 * X-Sync-Warning header nobody reads. Mirror the theta scan shape
 * (web/app/api/scanner/theta/scan/route.ts): status preserved, body stamped
 * `is_stale: true, scan_succeeded: false`. useSyncHook must surface a
 * body-level failure. GET /api/gex must not mark a stale snapshot HIT-fresh.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

const mockReadFile = vi.fn();
const mockStat = vi.fn().mockResolvedValue({ mtimeMs: Date.now() });
const mockReaddir = vi.fn().mockResolvedValue([]);
vi.mock("fs/promises", () => {
  const mocked = {
    readFile: (...args: unknown[]) => mockReadFile(...args),
    stat: (...args: unknown[]) => mockStat(...args),
    readdir: (...args: unknown[]) => mockReaddir(...args),
    writeFile: vi.fn().mockResolvedValue(undefined),
    mkdir: vi.fn().mockResolvedValue(undefined),
  };
  return { ...mocked, default: mocked };
});

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({
  radonFetch: (...args: unknown[]) => mockRadonFetch(...args),
  RadonApiError: class extends Error {
    status: number;
    constructor(status: number, detail: string) {
      super(`Radon API ${status}: ${detail}`);
      this.status = status;
    }
  },
}));

const mockExecute = vi.fn().mockResolvedValue({ rows: [] });
vi.mock("@/lib/db", () => ({ getDb: () => ({ execute: mockExecute }), resetDb: () => {} }));

beforeEach(() => {
  vi.resetModules();
  mockReadFile.mockReset();
  mockRadonFetch.mockReset();
  mockExecute.mockReset();
  mockExecute.mockResolvedValue({ rows: [] });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// R-643: POST catches must preserve status + stamp body-level failure markers
// ---------------------------------------------------------------------------

describe("POST /api/gex upstream failure (R-643)", () => {
  it("serves the cached fallback with a non-2xx status and body-level markers", async () => {
    mockRadonFetch.mockRejectedValue(new Error("upstream down"));
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: "2026-09-05T13:00:00Z",
      ticker: "SPX",
      net_gex: 321,
      history: [],
    }));

    const { POST } = await import("../app/api/gex/route");
    const res = await POST();
    expect(res.status).toBeGreaterThanOrEqual(500);
    const body = await res.json();
    expect(body.net_gex).toBe(321);
    expect(body.is_stale).toBe(true);
    expect(body.scan_succeeded).toBe(false);
  });

  it("stamps scan_succeeded: true on a fresh scan", async () => {
    mockRadonFetch.mockResolvedValue({
      scan_time: "2026-09-05T14:01:00Z",
      ticker: "SPX",
      net_gex: 456,
      history: [],
    });
    const { POST } = await import("../app/api/gex/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.scan_succeeded).toBe(true);
  });
});

describe("POST /api/regime upstream failure (R-643)", () => {
  it("serves the cached fallback with a non-2xx status and body-level markers", async () => {
    mockRadonFetch.mockRejectedValue(new Error("upstream down"));
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: "2026-09-05T13:00:00Z",
      date: "2026-09-05",
      market_open: true,
      cri: { score: 18, level: "LOW", components: { vix: 4, vvix: 4, correlation: 5, momentum: 5 } },
      history: [],
      spy_closes: [],
    }));

    const { POST } = await import("../app/api/regime/route");
    const res = await POST();
    expect(res.status).toBeGreaterThanOrEqual(500);
    const body = await res.json();
    expect(body.cri.score).toBe(18);
    expect(body.is_stale).toBe(true);
    expect(body.scan_succeeded).toBe(false);
  });
});

describe("POST /api/gamma-rotation upstream failure (R-643)", () => {
  it("serves the cached fallback with a non-2xx status and body-level markers", async () => {
    mockRadonFetch.mockRejectedValue(new Error("upstream down"));
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: "2026-09-05T13:00:00Z",
      signal: { grg_z: 1.23 },
      assets: { SPY: { ticker: "SPY" }, TLT: { ticker: "TLT" } },
      history: [],
    }));

    const { POST } = await import("../app/api/gamma-rotation/route");
    const res = await POST();
    expect(res.status).toBeGreaterThanOrEqual(500);
    const body = await res.json();
    expect(body.signal.grg_z).toBe(1.23);
    expect(body.is_stale).toBe(true);
    expect(body.scan_succeeded).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// R-643: useSyncHook surfaces body-level failure (header-only must fail)
// ---------------------------------------------------------------------------

describe("useSyncHook body-level failure (R-643)", () => {
  it("surfaces error when a 200 body carries scan_succeeded: false", async () => {
    const { useSyncHook } = await import("../lib/useSyncHook");
    const degraded = { last_sync: "old", is_stale: true, scan_succeeded: false };
    const headers = new Headers({ "X-Sync-Warning": "sync failed - serving cached data" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers,
      async json() { return degraded; },
    }));

    const { result } = renderHook(() =>
      useSyncHook<typeof degraded>({ endpoint: "/api/gex" }, true),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    await waitFor(() => expect(result.current.error).toBeTruthy());
    // Degraded cached body still renders.
    expect(result.current.data).toEqual(degraded);
  });

  it("keeps prior data and surfaces error when the POST returns non-2xx", async () => {
    const { useSyncHook } = await import("../lib/useSyncHook");
    const fresh = { last_sync: "fresh" };
    const fetchMock = vi.fn((_: unknown, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "GET") {
        return Promise.resolve({ ok: true, status: 200, headers: new Headers(), async json() { return fresh; } });
      }
      return Promise.resolve({
        ok: false,
        status: 502,
        headers: new Headers(),
        async json() { return { last_sync: "old", is_stale: true, scan_succeeded: false }; },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() =>
      useSyncHook<typeof fresh>({ endpoint: "/api/gex" }, true),
    );
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.data).toEqual(fresh);
  });
});

// ---------------------------------------------------------------------------
// R-660: GET /api/gex must not mark a stale snapshot HIT-fresh
// ---------------------------------------------------------------------------

describe("GET /api/gex staleness (R-660)", () => {
  it("marks a stale snapshot STALE in X-Cache-State and stamps is_stale in the body", async () => {
    mockRadonFetch.mockResolvedValue({}); // background rescan trigger
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: "2026-01-02T15:00:00Z", // months old
      market_open: true,
      ticker: "SPX",
      net_gex: 111,
      history: [],
    }));

    const { GET } = await import("../app/api/gex/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(res.headers.get("X-Cache-State")).toBe("STALE");
    const body = await res.json();
    expect(body.is_stale).toBe(true);
  });

  it("keeps HIT + is_stale: false for a fresh closed-market snapshot", async () => {
    mockRadonFetch.mockResolvedValue({});
    const { mostRecentSessionDate } = await import("../lib/marketSession");
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: `${mostRecentSessionDate()}T19:59:00Z`,
      market_open: false,
      ticker: "SPX",
      net_gex: 222,
      history: [],
    }));

    const { GET } = await import("../app/api/gex/route");
    const res = await GET();
    expect(res.headers.get("X-Cache-State")).toBe("HIT");
    const body = await res.json();
    expect(body.is_stale).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// T-470: a 4xx upstream REJECTION must pass through exactly (no 502 rewrite)
// and must NOT be masked by the >= 500 cache-fallback guard.
// ---------------------------------------------------------------------------

describe("POST scan routes: 4xx upstream status passthrough (T-470)", () => {
  it("gex: RadonApiError 429 yields exactly 429 with no cached body", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mockRadonFetch.mockRejectedValue(new RadonApiError(429, "rate limited"));
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: "2026-09-05T13:00:00Z",
      ticker: "SPX",
      net_gex: 321,
      history: [],
    }));

    const { POST } = await import("../app/api/gex/route");
    const res = await POST();
    expect(res.status).toBe(429);
    const body = await res.json();
    expect(body.scan_succeeded).toBe(false);
    // No cached snapshot may be served for a client error.
    expect(body.net_gex).toBeUndefined();
    expect(body.is_stale).toBeUndefined();
  });

  it("regime: RadonApiError 429 yields exactly 429 with no cached body", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mockRadonFetch.mockRejectedValue(new RadonApiError(429, "rate limited"));
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: "2026-09-05T13:00:00Z",
      date: "2026-09-05",
      market_open: true,
      cri: { score: 18, level: "LOW", components: { vix: 4, vvix: 4, correlation: 5, momentum: 5 } },
      history: [],
      spy_closes: [],
    }));

    const { POST } = await import("../app/api/regime/route");
    const res = await POST();
    expect(res.status).toBe(429);
    const body = await res.json();
    expect(body.scan_succeeded).toBe(false);
    expect(body.cri).toBeUndefined();
    expect(body.is_stale).toBeUndefined();
  });

  it("gamma-rotation: RadonApiError 429 yields exactly 429 with no cached body", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mockRadonFetch.mockRejectedValue(new RadonApiError(429, "rate limited"));
    mockReadFile.mockResolvedValue(JSON.stringify({
      scan_time: "2026-09-05T13:00:00Z",
      signal: { grg_z: 1.23 },
      assets: { SPY: { ticker: "SPY" }, TLT: { ticker: "TLT" } },
      history: [],
    }));

    const { POST } = await import("../app/api/gamma-rotation/route");
    const res = await POST();
    expect(res.status).toBe(429);
    const body = await res.json();
    expect(body.scan_succeeded).toBe(false);
    expect(body.signal).toBeUndefined();
    expect(body.is_stale).toBeUndefined();
  });
});
