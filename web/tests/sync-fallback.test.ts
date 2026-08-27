import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * sync-fallback.test.ts
 *
 * When sync fails, portfolio and orders fall back to their latest Turso
 * snapshots. 502 should only happen when refresh fails and no synced DB
 * snapshot exists.
 */

const mockReadDataFile = vi.fn();
vi.mock("@tools/data-reader", () => ({ readDataFile: mockReadDataFile }));

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

const mockReadOrdersSnapshotFromDb = vi.fn();
vi.mock("@/lib/orders/readOrdersFromDb", () => ({
  readOrdersSnapshotFromDb: mockReadOrdersSnapshotFromDb,
}));

const mockExecute = vi.fn().mockResolvedValue({ rows: [] });
vi.mock("@/lib/db", () => ({ getDb: () => ({ execute: mockExecute }), resetDb: () => {} }));

function mockPortfolioDb(portfolio: Record<string, unknown> | null) {
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

function makeOrders(lastSync: string) {
  return {
    last_sync: lastSync,
    open_orders: [],
    executed_orders: [],
    open_count: 0,
    executed_count: 0,
  };
}

describe("POST /api/portfolio — sync failure fallback", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    mockExecute.mockReset();
    mockExecute.mockResolvedValue({ rows: [] });
  });

  it("returns latest Turso portfolio data with 200 when ibSync fails", async () => {
    const snapshot = makePortfolio("2026-03-13T14:00:00Z");
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockPortfolioDb(snapshot);

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-03-13T14:00:00Z");
    expect(body.positions).toEqual([]);
    expect(mockRadonFetch).toHaveBeenCalledWith("/portfolio/sync", expect.objectContaining({ method: "POST" }));
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("sets X-Sync-Warning header when falling back to Turso data", async () => {
    const snapshot = makePortfolio("2026-03-13T14:00:00Z");
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockPortfolioDb(snapshot);

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();

    expect(response.headers.get("X-Sync-Warning")).toBeTruthy();
  });

  it("returns 502 only when sync fails AND no synced Turso snapshot exists", async () => {
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockPortfolioDb(null);

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();

    expect(response.status).toBe(502);
  });

  it("returns synced portfolio data with 200 when sync succeeds", async () => {
    const synced = makePortfolio("2026-03-13T15:00:00Z");
    mockRadonFetch.mockResolvedValue(synced);
    mockExecute.mockResolvedValue({ rows: [] });

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-03-13T15:00:00Z");
    expect(response.headers.get("X-Sync-Warning")).toBeNull();
  });
});

describe("GET /api/portfolio — cached snapshot only", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    mockExecute.mockReset();
    mockRadonFetch.mockReset();
  });

  it("fails without touching IB when the Turso portfolio read fails", async () => {
    mockExecute.mockRejectedValue(new Error("portfolio snapshot read timed out after 3000ms"));

    const { GET } = await import("../app/api/portfolio/route");
    const response = await GET();

    expect(response.status).toBe(503);
    expect(mockRadonFetch).not.toHaveBeenCalled();
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("fails without touching IB when no Turso portfolio snapshot exists", async () => {
    mockPortfolioDb(null);

    const { GET } = await import("../app/api/portfolio/route");
    const response = await GET();

    expect(response.status).toBe(503);
    expect(mockRadonFetch).not.toHaveBeenCalled();
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("serves a stale snapshot with a warning and no background IB sync", async () => {
    // `portfolio-sync` is an RTH_ONLY_SERVICES member, so while the market is
    // open isStale() measures age from TODAY'S OPEN, not from the snapshot. In
    // the first 10 minutes of the session even a months-old snapshot is inside
    // the window, no warning is set, and this read a null header — which is
    // exactly what CI hit at 09:36 ET. Pin the clock mid-session so the case
    // under test is staleness rather than the hour CI happened to run.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-03-20T18:00:00Z")); // Fri 14:00 ET
    try {
      const snapshot = makePortfolio("2026-03-13T15:45:00Z");
      mockPortfolioDb(snapshot);

      const { GET } = await import("../app/api/portfolio/route");
      const response = await GET();
      const body = await response.json();

      expect(response.status).toBe(200);
      expect(body.last_sync).toBe(snapshot.last_sync);
      expect(response.headers.get("X-Sync-Warning")).toContain("stale");
      expect(mockRadonFetch).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("POST /api/orders — sync failure fallback", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    mockReadOrdersSnapshotFromDb.mockReset();
  });

  it("returns latest Turso orders data with 200 when ibOrders sync fails", async () => {
    const snapshot = makeOrders("2026-03-13T14:00:00Z");
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockReadOrdersSnapshotFromDb.mockResolvedValue(snapshot);

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-03-13T14:00:00Z");
    expect(body.open_orders).toEqual([]);
    expect(mockRadonFetch).toHaveBeenCalledWith("/orders/refresh", expect.objectContaining({ method: "POST" }));
    expect(mockReadDataFile).not.toHaveBeenCalled();
  });

  it("sets X-Sync-Warning header when falling back to Turso orders", async () => {
    const snapshot = makeOrders("2026-03-13T14:00:00Z");
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockReadOrdersSnapshotFromDb.mockResolvedValue(snapshot);

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();

    expect(response.headers.get("X-Sync-Warning")).toBeTruthy();
  });

  it("returns 502 only when sync fails AND no synced Turso orders snapshot exists", async () => {
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockReadOrdersSnapshotFromDb.mockResolvedValue(makeOrders(""));

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();

    expect(response.status).toBe(502);
  });

  it("returns orders data with 200 when sync succeeds", async () => {
    const snapshot = makeOrders("2026-03-13T15:00:00Z");
    mockRadonFetch.mockResolvedValue({ ok: true });
    mockReadOrdersSnapshotFromDb.mockResolvedValue(snapshot);

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-03-13T15:00:00Z");
    expect(response.headers.get("X-Sync-Warning")).toBeNull();
  });
});
