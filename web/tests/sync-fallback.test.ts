import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * sync-fallback.test.ts
 *
 * When sync fails, routes must fall back to cached data and return 200.
 * 502 should only happen when both sync and cache are unavailable.
 */

const mockStat = vi.fn();
vi.mock("fs/promises", () => ({ stat: mockStat }));

const mockReadDataFile = vi.fn();
vi.mock("@tools/data-reader", () => ({ readDataFile: mockReadDataFile }));

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

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
    mockStat.mockResolvedValue({ mtimeMs: Date.now() });
  });

  it("returns cached portfolio data with 200 when ibSync fails", async () => {
    const cached = makePortfolio("2026-03-13T14:00:00Z");
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-03-13T14:00:00Z");
    expect(body.positions).toEqual([]);
    expect(mockRadonFetch).toHaveBeenCalledWith("/portfolio/sync", expect.objectContaining({ method: "POST" }));
  });

  it("sets X-Sync-Warning header when falling back to cached data", async () => {
    const cached = makePortfolio("2026-03-13T14:00:00Z");
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();

    expect(response.headers.get("X-Sync-Warning")).toBeTruthy();
  });

  it("returns 502 only when sync fails AND no cached data exists", async () => {
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockReadDataFile.mockResolvedValue({ ok: false, error: "File not found" });

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();

    expect(response.status).toBe(502);
  });

  it("returns cached portfolio data with 200 when sync succeeds", async () => {
    const synced = makePortfolio("2026-03-13T15:00:00Z");
    mockRadonFetch.mockResolvedValue(synced);
    mockReadDataFile.mockResolvedValue({ ok: true, data: synced });

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-03-13T15:00:00Z");
    expect(response.headers.get("X-Sync-Warning")).toBeNull();
  });
});

describe("POST /api/orders — sync failure fallback", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    mockStat.mockResolvedValue({ mtimeMs: Date.now() });
  });

  it("returns cached orders data with 200 when ibOrders sync fails", async () => {
    const cached = makeOrders("2026-03-13T14:00:00Z");
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-03-13T14:00:00Z");
    expect(body.open_orders).toEqual([]);
    expect(mockRadonFetch).toHaveBeenCalledWith("/orders/refresh", expect.objectContaining({ method: "POST" }));
  });

  it("sets X-Sync-Warning header when falling back to cached orders", async () => {
    const cached = makeOrders("2026-03-13T14:00:00Z");
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();

    expect(response.headers.get("X-Sync-Warning")).toBeTruthy();
  });

  it("returns 502 only when sync fails AND no cached orders exist", async () => {
    mockRadonFetch.mockRejectedValue(new Error("Connect call failed"));
    mockReadDataFile.mockResolvedValue({ ok: false, error: "File not found" });

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();

    expect(response.status).toBe(502);
  });

  it("returns orders data with 200 when sync succeeds", async () => {
    const cached = makeOrders("2026-03-13T15:00:00Z");
    mockRadonFetch.mockResolvedValue({ ok: true });
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-03-13T15:00:00Z");
    expect(response.headers.get("X-Sync-Warning")).toBeNull();
  });
});
