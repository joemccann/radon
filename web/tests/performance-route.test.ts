import { beforeEach, describe, expect, it, vi } from "vitest";

const mockReadFile = vi.fn();
const mockStat = vi.fn();

vi.mock("fs/promises", () => ({
  readFile: mockReadFile,
  stat: mockStat,
}));

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

describe("/api/performance route", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-13T16:10:00Z"));
    vi.clearAllMocks();
    mockReadFile.mockReset();
    mockStat.mockReset();
    mockRadonFetch.mockReset();
  });

  it("GET returns cached performance data when cache is fresh and aligned with portfolio", async () => {
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
      if (path.includes("portfolio.json")) {
        return JSON.stringify({
          last_sync: "2026-03-13T12:00:00Z",
          account_summary: { net_liquidation: 1_313_112.03 },
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

  it("GET refreshes inline when cached performance lags the current portfolio snapshot", async () => {
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
      if (path.includes("portfolio.json")) {
        return JSON.stringify({
          last_sync: "2026-03-11T13:37:14Z",
          account_summary: { net_liquidation: 1_313_112.03 },
        });
      }
      throw new Error(`unexpected read: ${path}`);
    });
    mockRadonFetch
      .mockResolvedValueOnce({
        as_of: "2026-03-11",
        last_sync: "2026-03-11T13:37:14Z",
      })
      .mockResolvedValueOnce({
        as_of: "2026-03-11",
        last_sync: "2026-03-11T13:37:14Z",
        summary: { ending_equity: 1_313_112.03 },
        series: [],
      });

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.as_of).toBe("2026-03-11");
    expect(body.summary.ending_equity).toBe(1_313_112.03);
    expect(mockRadonFetch).toHaveBeenCalledTimes(2);
    expect(mockRadonFetch).toHaveBeenNthCalledWith(
      1,
      "/portfolio/sync",
      expect.objectContaining({ method: "POST" }),
    );
    expect(mockRadonFetch).toHaveBeenNthCalledWith(
      2,
      "/performance",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("GET refreshes the portfolio snapshot before rebuilding when cached performance is behind current ET session", async () => {
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
      if (path.includes("portfolio.json")) {
        return JSON.stringify({
          last_sync: "2026-03-12T13:23:21Z",
          account_summary: { net_liquidation: 1_218_410.03 },
        });
      }
      throw new Error(`unexpected read: ${path}`);
    });
    mockRadonFetch
      .mockResolvedValueOnce({
        as_of: "2026-03-13",
        last_sync: "2026-03-13T20:02:06Z",
      })
      .mockResolvedValueOnce({
        as_of: "2026-03-13",
        last_sync: "2026-03-13T20:02:06Z",
        summary: { ending_equity: 1_250_902.19 },
        series: [],
      });

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.as_of).toBe("2026-03-13");
    expect(body.summary.ending_equity).toBe(1_250_902.19);
    expect(mockRadonFetch).toHaveBeenNthCalledWith(
      1,
      "/portfolio/sync",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("GET returns cached performance when portfolio refresh fails and cache is current", async () => {
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
      if (path.includes("portfolio.json")) {
        return JSON.stringify({
          last_sync: "2026-03-12T13:23:21Z",
          account_summary: { net_liquidation: 1_218_410.03 },
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
});
