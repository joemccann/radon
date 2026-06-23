import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Verifies that GET /api/portfolio triggers background sync via FastAPI
 * when the latest Turso portfolio snapshot is stale, without blocking the
 * response.
 */

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

const mockReadDataFile = vi.fn();
vi.mock("@tools/data-reader", () => ({ readDataFile: mockReadDataFile }));

const mockExecute = vi.fn();
vi.mock("@/lib/db", () => ({ getDb: () => ({ execute: mockExecute }) }));

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

function ageAgo(ageMs: number): string {
  return new Date(Date.now() - ageMs).toISOString();
}

function mockDbPortfolio(portfolio: Record<string, unknown> | null) {
  mockExecute.mockImplementation(async ({ sql }: { sql: string }) => {
    if (/FROM\s+portfolio_snapshots/i.test(sql)) {
      return {
        rows: portfolio
          ? [{ taken_at: portfolio.last_sync, payload: JSON.stringify(portfolio) }]
          : [],
      };
    }
    if (/FROM\s+journal/i.test(sql)) {
      return { rows: [] };
    }
    return { rows: [] };
  });
}

describe("GET /api/portfolio — stale-while-revalidate background sync", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    mockRadonFetch.mockResolvedValue({ ok: true });
    mockDbPortfolio(makePortfolio(ageAgo(10_000)));
  });

  it("triggers FastAPI background sync when the Turso snapshot is >60 s old", async () => {
    const portfolio = makePortfolio(ageAgo(90_000));
    mockDbPortfolio(portfolio);

    const { GET } = await import("../app/api/portfolio/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe(portfolio.last_sync);
    expect(mockRadonFetch).toHaveBeenCalledOnce();
    const [path, options] = mockRadonFetch.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/portfolio/background-sync");
    expect(options).toMatchObject({ method: "POST" });
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("does NOT trigger FastAPI sync when the Turso snapshot is <60 s old", async () => {
    mockDbPortfolio(makePortfolio(ageAgo(10_000)));

    const { GET } = await import("../app/api/portfolio/route");
    await GET();

    expect(mockRadonFetch).not.toHaveBeenCalled();
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("triggers background sync when no Turso snapshot exists", async () => {
    mockDbPortfolio(null);

    const { GET } = await import("../app/api/portfolio/route");
    const response = await GET();

    expect(response.status).toBe(404);
    expect(mockRadonFetch).toHaveBeenCalledOnce();
    const [path, options] = mockRadonFetch.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/portfolio/background-sync");
    expect(options).toMatchObject({ method: "POST" });
  });

  it("does not trigger a second sync when one is already in-flight", async () => {
    mockRadonFetch.mockReturnValue(new Promise(() => {}));
    mockDbPortfolio(makePortfolio(ageAgo(90_000)));

    const { GET } = await import("../app/api/portfolio/route");

    await GET();
    await GET();

    expect(mockRadonFetch).toHaveBeenCalledOnce();
  });
});
