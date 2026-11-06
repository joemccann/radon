/**
 * @vitest-environment node
 *
 * /api/skew — SPX 1M 25-delta put/call IV skew route (regime tab).
 *
 * GET-only: the series updates once per session (radon-skew.timer runs
 * fetch_skew.py directly), so there is no manual-scan POST. Reads through the
 * dbFirstRead chokepoint — Turso scan_snapshots row (service='skew') first,
 * last-in-memory fallback — and ALWAYS returns 200; absent data is the
 * contract's `missing: true` shape, never a 4xx.
 *
 * Mechanics mirror straddle-api.test.ts: @/lib/db backed by a real in-memory
 * libsql client seeded with the scan_snapshots schema; fs reads mocked.
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
    source: "unusual_whales",
    count: 730,
    current: {
      date: "2026-08-05",
      ratio: 1.292999,
      change: -0.12,
      put_iv: 0.159567,
      call_iv: 0.123408,
      expiry: "2026-09-18",
      dte: 44,
    },
    stats: { high: 0.13, low: -0.16, avg: 0.0004, stddev: 0.04 },
    series: [
      { date: "2026-08-04", ratio: 1.412999, change: 0.01 },
      { date: "2026-08-05", ratio: 1.292999, change: -0.12 },
    ],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('skew', ?, ?)`,
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

describe("GET /api/skew", () => {
  it("serves the latest Turso snapshot (newest row wins)", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: staleTime, count: 1 }));

    const { GET } = await import("../app/api/skew/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(730);
    expect((json.current as Payload).change).toBe(-0.12);
    expect(json.missing).toBeUndefined();
  });

  it("serves the last good read from memory when Turso has no rows", async () => {
    await insertSnapshot(buildPayload({ count: 729 }));

    const { GET } = await import("../app/api/skew/route");
    await GET();
    await db.execute("DELETE FROM scan_snapshots");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(729);
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/skew/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json).toEqual({
      missing: true,
      scan_time: null,
      count: 0,
      series: [],
      current: null,
      stats: null,
    });
  });

  it("only reads the skew service's snapshots", async () => {
    await db.execute({
      sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('straddle', ?, ?)`,
      args: [new Date().toISOString(), JSON.stringify(buildPayload({ count: 5 }))],
    });

    const { GET } = await import("../app/api/skew/route");
    const res = await GET();
    const json = await jsonOf(res);
    expect(json.missing).toBe(true);
  });

  it("declares force-dynamic per the snapshot route cache contract", async () => {
    const route = await import("../app/api/skew/route");
    expect(route.dynamic).toBe("force-dynamic");
  });

  it("disables intermediary caching for intraday snapshots", async () => {
    await insertSnapshot(buildPayload());
    const { GET } = await import("../app/api/skew/route");
    const res = await GET();
    const cacheControl = res.headers.get("cache-control") ?? "";
    expect(cacheControl).toContain("no-store");
  });

  it("expired snapshots are not returned as current", async () => {
    const old = buildPayload({ scan_time: "2026-01-01T21:45:00.000Z" });
    await insertSnapshot(old, old.scan_time as string);
    const { GET } = await import("../app/api/skew/route");
    expect(await jsonOf(await GET())).toMatchObject({ missing: true, count: 0 });
  });
});
