/**
 * @vitest-environment node
 *
 * /api/dispersion — VIX vs single-stock vs cross-sector dispersion, z-scored
 * since 2017 (regime tab DISPERSION).
 *
 * GET-only (radon-dispersion.timer runs the IB daily-bar sweep at 22:20 UTC
 * every calendar day). Reads through dbFirstRead — Turso scan_snapshots
 * (service='dispersion') first, disk data/dispersion.json fallback — and
 * ALWAYS returns 200; absent data is the contract's `missing: true` shape,
 * never a 4xx.
 *
 * Spec: docs/indicators/dispersion.md §D, §G.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: mockGetDb }));

const mockReadFile = vi.fn();
vi.mock("fs/promises", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs/promises")>();
  return { ...actual, readFile: (...args: unknown[]) => mockReadFile(...args) };
});

async function seedSchema(client: Client): Promise<void> {
  await client.execute(`CREATE TABLE scan_snapshots (
    service TEXT NOT NULL, scan_time TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (service, scan_time))`);
}

type Payload = Record<string, unknown>;

// Window-relative dates: sessions derived from today, never hardcoded.
function daysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

const DATA_DATE = daysAgo(1);
const DISK_DATA_DATE = daysAgo(2);

const SECTORS = ["XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"];

function seriesPoint(dateStr: string, zStock: number): Payload {
  return {
    date: dateStr,
    z_vix: -0.31,
    z_stock: zStock,
    z_sector: 2.41,
    vix: 14.43,
    stock_spread: 0.0712,
    sector_spread: 0.0241,
  };
}

function currentFor(dateStr: string, zStock: number): Payload {
  return {
    date: dateStr,
    z_vix: -0.31,
    z_stock: zStock,
    z_sector: 2.41,
    vix: 14.43,
    stock_spread: 0.0712,
    sector_spread: 0.0241,
    m60_vix: 15.9,
    m60_stock: 0.0834,
    m60_sector: 0.0302,
    n_stocks: 501,
    n_sectors: 11,
    regime: "BELOW THE SURFACE",
    surface_gap: 2.72,
  };
}

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    status: "ok",
    source: { prices: "ib", vix: "ib" },
    data_date: DATA_DATE,
    universe: { index: "SPX", n_constituents: 503, sectors: SECTORS },
    fetch: { ib_ok: 512, yahoo_ok: 2, failed: 1, failed_symbols: ["FOO"] },
    count: 2,
    current: currentFor(DATA_DATE, 2.38),
    stats: {
      base: { start: "2017-01-03", end: DATA_DATE, n: 2 },
      vix: { mean_60d: 18.9, stdev_60d: 6.1, z_min: -1.2, z_max: 5.3 },
      stock: { mean_60d: 0.061, stdev_60d: 0.014, z_min: -1.4, z_max: 4.1 },
      sector: { mean_60d: 0.019, stdev_60d: 0.006, z_min: -1.3, z_max: 3.9 },
      days_below_surface: 214,
      last_below_surface_date: DATA_DATE,
    },
    series: [seriesPoint(DISK_DATA_DATE, 2.3), seriesPoint(DATA_DATE, 2.38)],
    ...overrides,
  };
}

function diskPayload(scanTime?: string): Payload {
  return buildPayload({
    ...(scanTime ? { scan_time: scanTime } : {}),
    data_date: DISK_DATA_DATE,
    count: 1,
    current: currentFor(DISK_DATA_DATE, 2.3),
    series: [seriesPoint(DISK_DATA_DATE, 2.3)],
  });
}

async function insertSnapshot(payload: Payload, service = "dispersion"): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES (?, ?, ?)`,
    args: [service, payload.scan_time as string, JSON.stringify(payload)],
  });
}

const ENOENT = Object.assign(new Error("ENOENT: no such file"), { code: "ENOENT" });
const jsonOf = async (res: Response) => (await res.json()) as Record<string, unknown>;

beforeEach(async () => {
  vi.resetModules();
  db = createClient({ url: ":memory:" });
  await seedSchema(db);
  mockGetDb.mockReturnValue(db);
  mockReadFile.mockRejectedValue(ENOENT);
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  db.close();
  vi.restoreAllMocks();
});

describe("GET /api/dispersion", () => {
  it("serves the latest Turso snapshot over an older disk cache", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload(staleTime)));
    const { GET } = await import("../app/api/dispersion/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.data_date).toBe(DATA_DATE);
    expect((json.current as Payload).z_stock).toBe(2.38);
    expect((json.current as Payload).z_sector).toBe(2.41);
    expect((json.current as Payload).z_vix).toBe(-0.31);
    expect((json.current as Payload).regime).toBe("BELOW THE SURFACE");
    expect((json.current as Payload).surface_gap).toBe(2.72);
    expect(json.status).toBe("ok");
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload()));
    const { GET } = await import("../app/api/dispersion/route");
    const json = await jsonOf(await GET());
    expect(json.data_date).toBe(DISK_DATA_DATE);
    expect((json.series as Payload[]).length).toBe(1);
    expect((json.current as Payload).z_stock).toBe(2.3);
    expect(json.missing).toBeUndefined();
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/dispersion/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      missing: true,
      scan_time: null,
      status: null,
      source: null,
      data_date: null,
      universe: null,
      fetch: null,
      count: 0,
      current: null,
      stats: null,
      series: [],
    });
  });

  it("only reads the dispersion service's snapshots (no cross-service leak)", async () => {
    await insertSnapshot(buildPayload(), "vixts");
    const { GET } = await import("../app/api/dispersion/route");
    expect((await jsonOf(await GET())).missing).toBe(true);
  });

  it("carries universe, fetch and source blocks through to the client", async () => {
    await insertSnapshot(buildPayload());
    const { GET } = await import("../app/api/dispersion/route");
    const json = await jsonOf(await GET());
    expect(json.universe).toEqual({ index: "SPX", n_constituents: 503, sectors: SECTORS });
    expect(json.fetch).toEqual({ ib_ok: 512, yahoo_ok: 2, failed: 1, failed_symbols: ["FOO"] });
    expect(json.source).toEqual({ prices: "ib", vix: "ib" });
  });

  it("passes a stale_source status through verbatim", async () => {
    await insertSnapshot(buildPayload({ status: "stale_source" }));
    const { GET } = await import("../app/api/dispersion/route");
    const json = await jsonOf(await GET());
    expect(json.status).toBe("stale_source");
    expect(json.missing).toBeUndefined();
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/dispersion/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});

// ── R-332 / R-366 / R-450: the route obeys the catalog's 26h freshness budget ─
//
// The timer runs every calendar day at 22:20 UTC (weekend runs are
// no-new-session heartbeats), so a snapshot older than the catalog's 26h
// window means the writer is down. Serve `result.ok && result.fresh ? data : staleCollapse(...)`, and
// pass `isDegraded: isMissingPayload` so a newer missing:true heartbeat never
// outranks a complete older snapshot.

describe("GET /api/dispersion freshness budget", () => {
  it("collapses an eight-day-old snapshot to the stale shape", async () => {
    const ancient = new Date(Date.now() - 8 * 24 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: ancient }));
    const { GET } = await import("../app/api/dispersion/route");
    const json = await jsonOf(await GET());

    expect(json.stale).toBe(true);
    expect(json.missing).toBe(true);
    expect(json.scan_time).toBe(ancient);
    expect(json.current).toBeNull();
  });

  // R-450: the route budget is the catalog's 26h (serviceHealthWindows /
  // watchdog services.py), not a private 48h that let hours 26-48 render a
  // confident regime beside a `behind` writer age.
  it("collapses a 30h-old snapshot: the route budget matches the 26h catalog window", async () => {
    const thirtyHours = new Date(Date.now() - 30 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: thirtyHours }));
    const { GET } = await import("../app/api/dispersion/route");
    const json = await jsonOf(await GET());

    expect(json.stale).toBe(true);
    expect(json.missing).toBe(true);
    expect(json.scan_time).toBe(thirtyHours);
  });

  it("still serves a snapshot inside the 26h budget verbatim", async () => {
    const recent = new Date(Date.now() - 12 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: recent }));
    const { GET } = await import("../app/api/dispersion/route");
    const json = await jsonOf(await GET());

    expect(json.stale).toBeUndefined();
    expect(json.missing).toBeUndefined();
    expect((json.current as Payload).z_stock).toBe(2.38);
  });

  it("does not let a newer missing:true payload outrank a complete older one", async () => {
    const older = new Date(Date.now() - 6 * 60 * 60_000).toISOString();
    const newer = new Date(Date.now() - 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: older }));
    mockReadFile.mockResolvedValue(
      JSON.stringify({ missing: true, scan_time: newer, series: [], current: null }),
    );
    const { GET } = await import("../app/api/dispersion/route");
    const json = await jsonOf(await GET());

    expect(json.missing).toBeUndefined();
    expect((json.current as Payload).z_stock).toBe(2.38);
  });
});
