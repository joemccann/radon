import { beforeEach, describe, expect, it, vi } from "vitest";
import { PORTFOLIO_SNAPSHOT_CACHE_TTL_MS } from "../lib/portfolio/portfolioReadCache";

/**
 * Verifies that GET /api/portfolio is a read-only snapshot surface. Browser
 * polling must never initiate IB work; scheduled jobs and explicit POST own
 * synchronization. Snapshot age warnings follow portfolio-sync market windows.
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
  });

  it("serves RTH-stale Turso data with a warning without triggering IB sync", async () => {
    // Friday 15:00 ET (19:00 UTC) RTH; snapshot 15 minutes old > 10m open window
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-10T19:00:00.000Z"));
    try {
      const lastSync = new Date(Date.now() - 15 * 60_000).toISOString();
      const portfolio = makePortfolio(lastSync);
      mockDbPortfolio(portfolio);

      const { GET } = await import("../app/api/portfolio/route");
      const response = await GET();
      const body = await response.json();

      expect(response.status).toBe(200);
      expect(body.last_sync).toBe(portfolio.last_sync);
      expect(response.headers.get("X-Sync-Warning")).toContain("scheduled refresh window");
      expect(response.headers.get("X-Portfolio-Source")).toBe("turso-stale");
      expect(mockRadonFetch).not.toHaveBeenCalled();
      expect(mockReadDataFile).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not degrade on weekend when Friday snapshot is within closed window", async () => {
    // Saturday afternoon ET; Friday close snapshot is expected silence
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-11T18:00:00.000Z"));
    try {
      const fridayClose = "2026-07-10T20:00:00.000Z";
      mockDbPortfolio(makePortfolio(fridayClose));

      const { GET } = await import("../app/api/portfolio/route");
      const response = await GET();

      expect(response.status).toBe(200);
      expect(response.headers.get("X-Sync-Warning")).toBeNull();
      expect(mockRadonFetch).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does NOT trigger FastAPI sync when the Turso snapshot is fresh for RTH", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-10T19:00:00.000Z"));
    try {
      mockDbPortfolio(makePortfolio(new Date(Date.now() - 10_000).toISOString()));

      const { GET } = await import("../app/api/portfolio/route");
      const response = await GET();

      expect(response.headers.get("X-Sync-Warning")).toBeNull();
      expect(mockRadonFetch).not.toHaveBeenCalled();
      expect(mockReadDataFile).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("returns unavailable without touching IB when no Turso snapshot exists", async () => {
    mockDbPortfolio(null);

    const { GET } = await import("../app/api/portfolio/route");
    const response = await GET();

    expect(response.status).toBe(503);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("keeps the API accessor's single direct-read retry", async () => {
    const portfolio = makePortfolio("2026-07-10T20:00:00.000Z");
    mockExecute
      .mockRejectedValueOnce(new Error("first Turso read failed"))
      .mockResolvedValueOnce({
        rows: [{ taken_at: portfolio.last_sync, payload: JSON.stringify(portfolio) }],
      });

    const { GET } = await import("../app/api/portfolio/route");
    const response = await GET();

    expect(response.status).toBe(200);
    expect(mockExecute).toHaveBeenCalledTimes(2);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("bounds the RSC seed to one short DB attempt and absorbs its late rejection", async () => {
    vi.useFakeTimers();
    let rejectDb!: (error: Error) => void;
    mockExecute.mockReturnValue(new Promise((_resolve, reject) => {
      rejectDb = reject;
    }));
    try {
      const {
        PORTFOLIO_SEED_TIMEOUT_MS,
        readPortfolioSnapshotSeed,
      } = await import("../lib/portfolio/readPortfolioSnapshot.server");
      let settled = false;
      const seed = readPortfolioSnapshotSeed().then((value) => {
        settled = true;
        return value;
      });

      await vi.advanceTimersByTimeAsync(PORTFOLIO_SEED_TIMEOUT_MS - 1);
      expect(settled).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      await expect(seed).resolves.toBeUndefined();
      expect(mockExecute).toHaveBeenCalledTimes(1);

      // The direct read can reject after the page has already fallen back to
      // the client GET. Its rejection must remain observed, not surface as an
      // unhandled promise after the RSC response has completed.
      rejectDb(new Error("late Turso rejection"));
      await Promise.resolve();
      await Promise.resolve();
      expect(mockExecute).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("returns the cached snapshot payload from the RSC seed", async () => {
    vi.useFakeTimers();
    // Friday RTH so a fresh snapshot carries no age warning.
    vi.setSystemTime(new Date("2026-07-10T19:00:00.000Z"));
    try {
      const portfolio = makePortfolio(new Date(Date.now() - 10_000).toISOString());
      portfolio.bankroll = 137_425.5;
      mockDbPortfolio(portfolio);

      const { readPortfolioSnapshotSeed } = await import(
        "../lib/portfolio/readPortfolioSnapshot.server"
      );
      const result = await readPortfolioSnapshotSeed();

      expect(result?.data.bankroll).toBe(137_425.5);
      expect(result?.data.last_sync).toBe(portfolio.last_sync);
      expect(result?.warning).toBeNull();
      expect(mockRadonFetch).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps repeated weekend GETs free of IB side effects", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-11T18:00:00.000Z"));
    try {
      mockDbPortfolio(makePortfolio("2026-07-10T20:00:00.000Z"));

      const { GET } = await import("../app/api/portfolio/route");

      await GET();
      await GET();

      expect(mockRadonFetch).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("warns when a recent snapshot is served from memory after a Turso failure", async () => {
    vi.useFakeTimers();
    // Friday RTH so a fresh snapshot is not age-stale; only Turso error warns
    vi.setSystemTime(new Date("2026-07-10T20:00:00Z"));
    process.env.RADON_DB_CACHE_FORCE = "1";
    try {
      mockDbPortfolio(makePortfolio(new Date(Date.now() - 10_000).toISOString()));
      const { GET } = await import("../app/api/portfolio/route");

      const fresh = await GET();
      expect(fresh.headers.get("X-Sync-Warning")).toBeNull();

      vi.advanceTimersByTime(PORTFOLIO_SNAPSHOT_CACHE_TTL_MS + 1);
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
