import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Verifies that GET /api/portfolio is a read-only snapshot surface. Browser
 * polling must never initiate IB work; scheduled jobs and explicit POST own
 * synchronization.
 */

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

const mockReadDataFile = vi.fn();
vi.mock("@tools/data-reader", () => ({ readDataFile: mockReadDataFile }));

const mockExecute = vi.fn();
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => ({ execute: mockExecute }) }));

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

describe("GET /api/portfolio — cache-only polling", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    mockRadonFetch.mockResolvedValue({ ok: true });
    mockDbPortfolio(makePortfolio(ageAgo(10_000)));
  });

  it("serves stale Turso data with a warning without triggering IB sync", async () => {
    const portfolio = makePortfolio(ageAgo(90_000));
    mockDbPortfolio(portfolio);

    const { GET } = await import("../app/api/portfolio/route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe(portfolio.last_sync);
    expect(response.headers.get("X-Sync-Warning")).toContain("stale");
    expect(response.headers.get("X-Portfolio-Source")).toBe("turso-stale");
    expect(mockRadonFetch).not.toHaveBeenCalled();
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("does NOT trigger FastAPI sync when the Turso snapshot is <60 s old", async () => {
    mockDbPortfolio(makePortfolio(ageAgo(10_000)));

    const { GET } = await import("../app/api/portfolio/route");
    await GET();

    expect(mockRadonFetch).not.toHaveBeenCalled();
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("returns unavailable without touching IB when no Turso snapshot exists", async () => {
    mockDbPortfolio(null);

    const { GET } = await import("../app/api/portfolio/route");
    const response = await GET();

    expect(response.status).toBe(503);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("keeps repeated stale GETs free of IB side effects", async () => {
    mockDbPortfolio(makePortfolio(ageAgo(90_000)));

    const { GET } = await import("../app/api/portfolio/route");

    await GET();
    await GET();

    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("warns when a recent snapshot is served from memory after a Turso failure", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-10T20:00:00Z"));
    process.env.RADON_DB_CACHE_FORCE = "1";
    try {
      mockDbPortfolio(makePortfolio(ageAgo(10_000)));
      const { GET } = await import("../app/api/portfolio/route");

      const fresh = await GET();
      expect(fresh.headers.get("X-Sync-Warning")).toBeNull();

      vi.advanceTimersByTime(3_001);
      mockExecute.mockRejectedValue(new Error("turso down"));
      const degraded = await GET();

      expect(degraded.status).toBe(200);
      expect(degraded.headers.get("X-Sync-Warning")).toContain("Turso read failed");
      expect(degraded.headers.get("X-Portfolio-Source")).toBe("turso-stale");
      expect(mockRadonFetch).not.toHaveBeenCalled();
    } finally {
      delete process.env.RADON_DB_CACHE_FORCE;
      vi.useRealTimers();
    }
  });
});
