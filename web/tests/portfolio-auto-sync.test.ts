import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * Verifies that GET /api/portfolio triggers background sync via FastAPI
 * when portfolio.json is stale, without blocking the response.
 */

// Mock fs/stat so we can control staleness.
const mockStat = vi.fn();
vi.mock("fs/promises", () => ({ stat: mockStat }));

// Mock read from cached data file.
const mockReadDataFile = vi.fn();
vi.mock("@tools/data-reader", () => ({ readDataFile: mockReadDataFile }));

// Mock FastAPI client.
const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

/** A minimal valid PortfolioData object */
function makePortfolio(lastSync: string) {
  return {
    bankroll: 100_000,
    peak_value: 100_000,
    last_sync: lastSync,
    positions: [],
    total_deployed_pct: 0,
    total_deployed_dollars: 0,
    remaining_capacity_pct: 100,
    position_count: 0,
    defined_risk_count: 0,
    undefined_risk_count: 0,
    avg_kelly_optimal: null,
  };
}

/** Returns an ISO timestamp that is `ageMs` milliseconds in the past */
function ageAgo(ageMs: number): string {
  return new Date(Date.now() - ageMs).toISOString();
}

describe("GET /api/portfolio — stale-while-revalidate background sync", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    mockRadonFetch.mockResolvedValue({ ok: true });
  });

  it("triggers FastAPI background sync when portfolio.json mtime is >60 s old", async () => {
    const staleMtime = new Date(Date.now() - 90_000);
    mockStat.mockResolvedValue({ mtimeMs: staleMtime.getTime() });

    const portfolio = makePortfolio(ageAgo(90_000));
    mockReadDataFile.mockResolvedValue({ ok: true, data: portfolio });

    const { GET } = await import("../app/api/portfolio/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe(portfolio.last_sync);
    expect(mockRadonFetch).toHaveBeenCalledOnce();
    const [path, options] = mockRadonFetch.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/portfolio/background-sync");
    expect(options).toMatchObject({ method: "POST" });
  });

  it("does NOT trigger FastAPI sync when portfolio.json mtime is <60 s old (fresh)", async () => {
    const freshMtime = new Date(Date.now() - 10_000);
    mockStat.mockResolvedValue({ mtimeMs: freshMtime.getTime() });
    const portfolio = makePortfolio(ageAgo(10_000));
    mockReadDataFile.mockResolvedValue({ ok: true, data: portfolio });

    const { GET } = await import("../app/api/portfolio/route");
    await GET();

    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("triggers background sync when stat() throws (file missing counts as stale)", async () => {
    mockStat.mockRejectedValue(new Error("ENOENT"));
    mockReadDataFile.mockResolvedValue({ ok: false, error: "not found" });

    const { GET } = await import("../app/api/portfolio/route");
    const response = await GET();

    expect(response.status).toBe(404);
    expect(mockRadonFetch).toHaveBeenCalledOnce();
    const [path, options] = mockRadonFetch.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/portfolio/background-sync");
    expect(options).toMatchObject({ method: "POST" });
  });

  it("does not trigger a second sync when one is already in-flight", async () => {
    mockRadonFetch.mockReturnValue(new Promise(() => {})); // never-resolving in-flight request

    const staleMtime = new Date(Date.now() - 90_000);
    mockStat.mockResolvedValue({ mtimeMs: staleMtime.getTime() });
    const portfolio = makePortfolio(ageAgo(90_000));
    mockReadDataFile.mockResolvedValue({ ok: true, data: portfolio });

    const { GET } = await import("../app/api/portfolio/route");

    await GET();
    await GET();

    expect(mockRadonFetch).toHaveBeenCalledOnce();
  });
});
