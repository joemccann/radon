import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * sync-fallback.test.ts
 *
 * When POST /api/portfolio or POST /api/orders sync fails (e.g. IB Gateway
 * unreachable), the routes must fall back to cached data files and return 200
 * instead of 502.  502 should only occur when both sync AND cache fail.
 */

// ---------------------------------------------------------------------------
// Module-level mocks
// ---------------------------------------------------------------------------

const mockStat = vi.fn();
vi.mock("fs/promises", () => ({
  stat: mockStat,
}));

vi.mock("child_process", () => ({
  spawn: vi.fn().mockReturnValue({
    stdout: { on: vi.fn() },
    stderr: { on: vi.fn() },
    on: vi.fn(),
    unref: vi.fn(),
  }),
}));

const mockReadDataFile = vi.fn();
vi.mock("@tools/data-reader", () => ({
  readDataFile: mockReadDataFile,
}));

const mockIbSync = vi.fn();
vi.mock("@tools/wrappers/ib-sync", () => ({
  ibSync: (...args: unknown[]) => mockIbSync(...args),
}));

const mockIbOrders = vi.fn();
vi.mock("@tools/wrappers/ib-orders", () => ({
  ibOrders: (...args: unknown[]) => mockIbOrders(...args),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// POST /api/portfolio — sync failure fallback
// ---------------------------------------------------------------------------

describe("POST /api/portfolio — sync failure falls back to cached data", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    // Fresh file (not stale) to prevent background sync from firing
    mockStat.mockResolvedValue({ mtimeMs: Date.now() });
  });

  it("returns cached portfolio data with 200 when ibSync fails", async () => {
    const cached = makePortfolio("2026-03-13T14:00:00Z");

    // ibSync fails (IB Gateway unreachable)
    mockIbSync.mockResolvedValue({
      ok: false,
      exitCode: 1,
      stderr: 'Connect call failed (\'127.0.0.1\', 4001)',
    });

    // But cached data file exists
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-03-13T14:00:00Z");
    expect(body.positions).toEqual([]);
  });

  it("sets X-Sync-Warning header when falling back to cached data", async () => {
    const cached = makePortfolio("2026-03-13T14:00:00Z");

    mockIbSync.mockResolvedValue({
      ok: false,
      exitCode: 1,
      stderr: "Connection refused",
    });
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();

    expect(response.headers.get("X-Sync-Warning")).toBeTruthy();
  });

  it("returns 502 only when sync fails AND no cached data exists", async () => {
    mockIbSync.mockResolvedValue({
      ok: false,
      exitCode: 1,
      stderr: "Connection refused",
    });

    // Cache file also missing
    mockReadDataFile.mockResolvedValue({ ok: false, error: "File not found" });

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();

    expect(response.status).toBe(502);
  });

  it("returns fresh synced data with 200 when ibSync succeeds", async () => {
    const fresh = makePortfolio("2026-03-13T15:00:00Z");

    mockIbSync.mockResolvedValue({ ok: true, data: fresh });

    const { POST } = await import("../app/api/portfolio/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-03-13T15:00:00Z");
    expect(response.headers.get("X-Sync-Warning")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// POST /api/orders — sync failure fallback
// ---------------------------------------------------------------------------

describe("POST /api/orders — sync failure falls back to cached data", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  it("returns cached orders data with 200 when ibOrders sync fails", async () => {
    const cached = makeOrders("2026-03-13T14:00:00Z");

    // ibOrders sync fails
    mockIbOrders.mockResolvedValue({
      ok: false,
      exitCode: 1,
      stderr: 'Connect call failed (\'127.0.0.1\', 4001)',
    });

    // But cached orders file exists
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-03-13T14:00:00Z");
    expect(body.open_orders).toEqual([]);
  });

  it("sets X-Sync-Warning header when falling back to cached orders", async () => {
    const cached = makeOrders("2026-03-13T14:00:00Z");

    mockIbOrders.mockResolvedValue({
      ok: false,
      exitCode: 1,
      stderr: "Connection refused",
    });
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();

    expect(response.headers.get("X-Sync-Warning")).toBeTruthy();
  });

  it("returns 502 only when sync fails AND no cached orders exist", async () => {
    mockIbOrders.mockResolvedValue({
      ok: false,
      exitCode: 1,
      stderr: "Connection refused",
    });

    // Cache file also missing
    mockReadDataFile.mockResolvedValue({ ok: false, error: "File not found" });

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();

    expect(response.status).toBe(502);
  });

  it("returns fresh synced data with 200 when ibOrders succeeds", async () => {
    const cached = makeOrders("2026-03-13T15:00:00Z");

    mockIbOrders.mockResolvedValue({ ok: true, stderr: "" });
    mockReadDataFile.mockResolvedValue({ ok: true, data: cached });

    const { POST } = await import("../app/api/orders/route");
    const response = await POST();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.last_sync).toBe("2026-03-13T15:00:00Z");
    expect(response.headers.get("X-Sync-Warning")).toBeNull();
  });
});
