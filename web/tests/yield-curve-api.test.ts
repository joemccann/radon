/**
 * @vitest-environment node
 *
 * /api/yield-curve — 10Y-2Y Treasury spread series route (CURVE regime tab).
 *
 * GET-only: the series updates daily (radon-yield-curve timer runs
 * fetch_yield_curve.py directly), so there is no manual-scan POST. Reads
 * through the dbFirstRead chokepoint — Turso scan_snapshots row
 * (service='yield-curve') first, last-in-memory fallback — and
 * ALWAYS returns 200; absent data is the contract's `missing: true` shape,
 * never a 4xx.
 *
 * Mechanics mirror margin-debt-api.test.ts: @/lib/db backed by a real
 * in-memory libsql client seeded with the scan_snapshots schema; fs mocked.
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

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    source: "treasury",
    count: 146,
    current: { date: "2026-07-31", y3m: 3.83, y2: 4.28, y10: 4.75, spread: 0.47 },
    series: [
      {
        date: "2026-07-30",
        y3m: 3.82,
        y2: 4.23,
        y10: 4.68,
        spread: 0.45,
        spx_close: 7560.2,
      },
      {
        date: "2026-07-31",
        y3m: 3.83,
        y2: 4.28,
        y10: 4.75,
        spread: 0.47,
        spx_close: 7585.17,
      },
    ],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('yield-curve', ?, ?)`,
    args: [scanTime ?? (payload.scan_time as string), JSON.stringify(payload)],
  });
}


async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  return (await res.json()) as Record<string, unknown>;
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

describe("GET /api/yield-curve", () => {
  it("serves the latest Turso snapshot (newest row wins)", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: staleTime, count: 1 }));

    const { GET } = await import("../app/api/yield-curve/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(146);
    expect((json.current as Payload).spread).toBe(0.47);
    expect(json.missing).toBeUndefined();
  });

  it("serves the last good read from memory when Turso has no rows", async () => {
    await insertSnapshot(buildPayload({ count: 145 }));

    const { GET } = await import("../app/api/yield-curve/route");
    await GET();
    await db.execute("DELETE FROM scan_snapshots");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(145);
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/yield-curve/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json).toEqual({
      missing: true,
      scan_time: null,
      count: 0,
      series: [],
      current: null,
    });
  });

  it("only reads the yield-curve service's snapshots", async () => {
    await db.execute({
      sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('margin-debt', ?, ?)`,
      args: [new Date().toISOString(), JSON.stringify(buildPayload({ count: 5 }))],
    });

    const { GET } = await import("../app/api/yield-curve/route");
    const res = await GET();
    const json = await jsonOf(res);
    expect(json.missing).toBe(true);
  });

  it("declares force-dynamic per the snapshot route cache contract", async () => {
    const route = await import("../app/api/yield-curve/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
