import { beforeEach, describe, expect, it, vi } from "vitest";

const mockReadFile = vi.fn();
const mockStat = vi.fn();

vi.mock("fs/promises", () => ({
  readFile: mockReadFile,
  stat: mockStat,
}));

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

const mockGetDb = vi.fn();
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: mockGetDb }));

function mockDbSnapshots({
  performance = null,
  portfolio = null,
}: {
  performance?: Record<string, unknown> | null;
  portfolio?: Record<string, unknown> | null;
} = {}) {
  mockGetDb.mockReturnValue({
    execute: vi.fn(async ({ sql }: { sql: string }) => {
      if (/FROM\s+performance_snapshots/i.test(sql)) {
        return {
          rows: performance
            ? [
                {
                  taken_at: String(performance.last_sync ?? performance.as_of ?? "2026-03-13T12:00:00Z"),
                  payload: JSON.stringify(performance),
                },
              ]
            : [],
        };
      }
      if (/FROM\s+portfolio_snapshots/i.test(sql)) {
        return {
          rows: portfolio ? [{ payload: JSON.stringify(portfolio) }] : [],
        };
      }
      return { rows: [] };
    }),
  });
}

describe("/api/performance route", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-13T16:10:00Z"));
    vi.clearAllMocks();
    mockReadFile.mockReset();
    mockStat.mockReset();
    mockRadonFetch.mockReset();
    mockGetDb.mockReset();
    mockDbSnapshots();
  });

  it("GET returns cached performance data when cache is fresh and aligned with portfolio", async () => {
    mockDbSnapshots({
      portfolio: {
        last_sync: "2026-03-13T12:00:00Z",
        account_summary: { net_liquidation: 1_313_112.03 },
      },
    });
    mockStat.mockResolvedValue({ mtimeMs: Date.now() });
    mockReadFile.mockImplementation(async (path: string) => {
      if (path.includes("performance.json")) {
        return JSON.stringify({
          as_of: "2026-03-13",
          last_sync: "2026-03-13T12:00:00Z",
          summary: { sharpe_ratio: 1.2 },
          series: [],
        });
      }
      throw new Error(`unexpected read: ${path}`);
    });

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.as_of).toBe("2026-03-13");
        expect(body.summary.sharpe_ratio).toBe(1.2);
        expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("GET returns stale cache + triggers background rebuild when cached performance lags the current portfolio snapshot (SWR)", async () => {
    mockDbSnapshots({
      portfolio: {
        last_sync: "2026-03-11T13:37:14Z",
        account_summary: { net_liquidation: 1_313_112.03 },
      },
    });
    mockStat.mockResolvedValue({ mtimeMs: Date.now() });
    mockReadFile.mockImplementation(async (path: string) => {
      if (path.includes("performance.json")) {
        return JSON.stringify({
          as_of: "2026-03-10",
          last_sync: "2026-03-10T18:55:00Z",
          summary: { ending_equity: 1_063_031.86 },
          series: [],
        });
      }
      throw new Error(`unexpected read: ${path}`);
    });
    mockRadonFetch
      .mockResolvedValueOnce({
        last_sync: "2026-03-11T13:37:14Z",
      })
      .mockResolvedValueOnce({ status: "accepted" });

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    // SWR: returns stale cache immediately
    expect(res.status).toBe(200);
    expect(body.as_of).toBe("2026-03-10");
    expect(body.summary.ending_equity).toBe(1_063_031.86);
    // Should have called portfolio/sync + background trigger
    expect(mockRadonFetch).toHaveBeenCalledTimes(2);
    expect(mockRadonFetch).toHaveBeenNthCalledWith(
      1,
      "/portfolio/sync",
      expect.objectContaining({ method: "POST" }),
    );
    expect(mockRadonFetch).toHaveBeenNthCalledWith(
      2,
      "/performance/background",
      expect.objectContaining({ method: "POST", timeout: 5_000 }),
    );
  });

  it("GET returns stale cache + triggers background rebuild when perf is behind current ET session (SWR)", async () => {
    mockDbSnapshots({
      portfolio: {
        last_sync: "2026-03-12T13:23:21Z",
        account_summary: { net_liquidation: 1_218_410.03 },
      },
    });
    mockStat.mockResolvedValue({ mtimeMs: Date.now() });
    mockReadFile.mockImplementation(async (path: string) => {
      if (path.includes("performance.json")) {
        return JSON.stringify({
          as_of: "2026-03-12",
          last_sync: "2026-03-12T13:23:21Z",
          summary: { ending_equity: 1_218_410.03 },
          series: [],
        });
      }
      throw new Error(`unexpected read: ${path}`);
    });
    mockRadonFetch
      .mockResolvedValueOnce({
        last_sync: "2026-03-13T20:02:06Z",
      })
      .mockResolvedValueOnce({ status: "accepted" });

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    // SWR: returns stale cache immediately
    expect(res.status).toBe(200);
    expect(body.as_of).toBe("2026-03-12");
    expect(body.summary.ending_equity).toBe(1_218_410.03);
    // Portfolio sync + background trigger
    expect(mockRadonFetch).toHaveBeenNthCalledWith(
      1,
      "/portfolio/sync",
      expect.objectContaining({ method: "POST" }),
    );
    expect(mockRadonFetch).toHaveBeenNthCalledWith(
      2,
      "/performance/background",
      expect.objectContaining({ method: "POST", timeout: 5_000 }),
    );
  });

  it("GET returns cached performance when portfolio refresh fails and cache is current", async () => {
    mockDbSnapshots({
      portfolio: {
        last_sync: "2026-03-12T13:23:21Z",
        account_summary: { net_liquidation: 1_218_410.03 },
      },
    });
    mockStat.mockResolvedValue({ mtimeMs: Date.now() });
    mockReadFile.mockImplementation(async (path: string) => {
      if (path.includes("performance.json")) {
        return JSON.stringify({
          as_of: "2026-03-12",
          last_sync: "2026-03-12T13:23:21Z",
          summary: { ending_equity: 1_218_410.03 },
          series: [],
        });
      }
      throw new Error(`unexpected read: ${path}`);
    });
    mockRadonFetch.mockRejectedValue(new Error("IB unavailable"));

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.as_of).toBe("2026-03-12");
    expect(mockRadonFetch).toHaveBeenCalledTimes(1);
    expect(mockRadonFetch).toHaveBeenCalledWith("/portfolio/sync", expect.objectContaining({ method: "POST" }));
  });

  it("POST runs the API sync and returns generated performance JSON", async () => {
    const payload = {
      as_of: "2026-03-10",
      last_sync: "2026-03-10T18:55:00Z",
      summary: { sharpe_ratio: 1.84 },
      series: [{ date: "2026-01-02", equity: 1_000_000 }],
    };
    mockRadonFetch.mockResolvedValue(payload);

    const { POST } = await import("../app/api/performance/route");
    const res = await POST();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.summary.sharpe_ratio).toBe(1.84);
    expect(mockRadonFetch).toHaveBeenCalledOnce();
    expect(mockRadonFetch).toHaveBeenCalledWith("/performance", expect.objectContaining({ method: "POST" }));
  });

  // ---- SWR-specific tests ----

  it("GET SWR: returns stale cache immediately and triggers background rebuild", async () => {
    mockDbSnapshots({ portfolio: { last_sync: "2026-03-13T12:00:00Z" } });
    // Stale: mtime is 20 minutes ago
    mockStat.mockResolvedValue({ mtimeMs: Date.now() - 20 * 60_000 });
    mockReadFile.mockImplementation(async (path: string) => {
      if (path.includes("performance.json")) {
        return JSON.stringify({
          as_of: "2026-03-13",
          last_sync: "2026-03-13T12:00:00Z",
          summary: { sharpe_ratio: 1.2 },
          series: [],
        });
      }
      throw new Error(`unexpected read: ${path}`);
    });
    // Background trigger should fire-and-forget
    mockRadonFetch.mockResolvedValue({ status: "accepted" });

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.summary.sharpe_ratio).toBe(1.2);
    // Should call background endpoint, not the blocking one
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/performance/background",
      expect.objectContaining({ method: "POST", timeout: 5_000 }),
    );
  });

  it("GET cold start: blocks on rebuild when no cache exists", async () => {
    mockStat.mockRejectedValue(new Error("ENOENT"));
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    mockRadonFetch.mockResolvedValue({
      as_of: "2026-03-13",
      last_sync: "2026-03-13T16:00:00Z",
      summary: { total_return: 0.18 },
      series: [],
    });

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.summary.total_return).toBe(0.18);
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/performance",
      expect.objectContaining({ method: "POST", timeout: 180_000 }),
    );
  });

  it("GET cold start: returns 502 when rebuild fails and no cache", async () => {
    mockStat.mockRejectedValue(new Error("ENOENT"));
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    mockRadonFetch.mockRejectedValue(new Error("FastAPI down"));

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(502);
    expect(body.error).toContain("FastAPI down");
  });

  it("GET SWR: background trigger failure is swallowed — stale cache still returned", async () => {
    mockDbSnapshots({ portfolio: { last_sync: "2026-03-13T12:00:00Z" } });
    mockStat.mockResolvedValue({ mtimeMs: Date.now() - 20 * 60_000 });
    mockReadFile.mockImplementation(async (path: string) => {
      if (path.includes("performance.json")) {
        return JSON.stringify({
          as_of: "2026-03-13",
          last_sync: "2026-03-13T12:00:00Z",
          summary: { ending_equity: 100_000 },
          series: [],
        });
      }
      throw new Error("not found");
    });
    mockRadonFetch.mockRejectedValue(new Error("timeout"));

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.summary.ending_equity).toBe(100_000);
  });
});
