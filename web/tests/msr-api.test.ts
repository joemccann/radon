/**
 * @vitest-environment node
 *
 * /api/msr — move surprise ratio (SPX, NDX, RUT) route (regime tab).
 *
 * GET-only: radon-msr.timer runs fetch_msr.py daily, so there is no
 * manual-scan POST. Reads through the dbFirstRead chokepoint (Turso
 * scan_snapshots row, service='msr', then the last good read in memory) and
 * ALWAYS returns 200; absent data is the contract's `missing: true` shape.
 *
 * Mechanics mirror straddle-api.test.ts: @/lib/db backed by a real
 * in-memory libsql client seeded with the scan_snapshots schema.
 * Spec: docs/indicators/msr.md.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: mockGetDb,
}));

async function seedSchema(client: Client): Promise<void> {
  await client.execute(`CREATE TABLE scan_snapshots (
    service TEXT NOT NULL,
    scan_time TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (service, scan_time))`);
}

type Payload = Record<string, unknown>;

function spxSummary(): Payload {
  return {
    iv_symbol: "VIX",
    current: {
      date: "2026-11-06", msr: 0.011162, move_pct: 0.314534,
      close: 7376.86, iv_prior: 28.18, rank: 3736,
    },
    week_max: { date: "2026-11-05", msr: 0.262231, move_pct: -4.355658, iv_prior: 16.61, rank: 1 },
    stats: {
      count: 4779, max: 0.262231, max_date: "2026-11-05",
      p50: 0.029424, p90: 0.081531, p99: 0.149252,
    },
    top: [{ date: "2026-11-05", msr: 0.262231, move_pct: -4.355658 }],
  };
}

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    start_date: "2007-11-08",
    source_last_modified: {
      spx: "Sat, 07 Nov 2026 02:11:00 GMT",
      vix: "Sat, 07 Nov 2026 02:21:00 GMT",
      vxn: "Fri, 06 Nov 2026 22:31:00 GMT",
      rvx: "Fri, 06 Nov 2026 22:31:00 GMT",
    },
    close_sources: { SPX: ["cboe"], NDX: ["ib"], RUT: ["ib", "yahoo"] },
    indexes: {
      SPX: spxSummary(),
      NDX: { iv_symbol: "VXN", current: null, week_max: null, stats: null, top: [] },
      RUT: { iv_symbol: "RVX", current: null, week_max: null, stats: null, top: [] },
    },
    series: [
      { date: "2026-11-05", spx: 0.262231, ndx: null, rut: 0.225762 },
      { date: "2026-11-06", spx: 0.011162, ndx: null, rut: 0.030719 },
    ],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, service = "msr"): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES (?, ?, ?)`,
    args: [service, payload.scan_time as string, JSON.stringify(payload)],
  });
}

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  return (await res.json()) as Record<string, unknown>;
}

function spxCurrent(json: Record<string, unknown>): Payload {
  const indexes = json.indexes as Record<string, Payload>;
  return indexes.SPX.current as Payload;
}

beforeEach(async () => {
  vi.resetModules();
  db = createClient({ url: ":memory:" });
  await seedSchema(db);
  mockGetDb.mockReturnValue(db);
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  db.close();
  vi.restoreAllMocks();
});

describe("GET /api/msr", () => {
  it("serves the latest Turso snapshot (newest row wins)", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: staleTime, start_date: "stale" }));

    const { GET } = await import("../app/api/msr/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.start_date).toBe("2007-11-08");
    expect(spxCurrent(json).rank).toBe(3736);
    expect(spxCurrent(json).msr).toBe(0.011162);
    expect((json.series as unknown[]).length).toBe(2);
    expect(json.missing).toBeUndefined();
  });

  it("serves the last good read from memory when Turso has no rows", async () => {
    await insertSnapshot(buildPayload({ start_date: "2007-11-09" }));

    const { GET } = await import("../app/api/msr/route");
    await GET();
    await db.execute("DELETE FROM scan_snapshots");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.start_date).toBe("2007-11-09");
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/msr/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json).toEqual({
      missing: true,
      scan_time: null,
      start_date: null,
      indexes: null,
      series: [],
    });
  });

  it("only reads the msr service's snapshots", async () => {
    await insertSnapshot(buildPayload(), "straddle");
    await insertSnapshot(buildPayload(), "msr-shadow");

    const { GET } = await import("../app/api/msr/route");
    const res = await GET();
    const json = await jsonOf(res);
    expect(json.missing).toBe(true);
  });

  it("declares force-dynamic per the snapshot route cache contract", async () => {
    const route = await import("../app/api/msr/route");
    expect(route.dynamic).toBe("force-dynamic");
  });

  it("declares the read capability", async () => {
    const route = await import("../app/api/msr/route");
    expect(route.radonCapability).toBe("read");
  });
});
