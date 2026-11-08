/**
 * @vitest-environment node
 *
 * /api/cor — SPX implied correlation route (regime tab).
 *
 * GET-only: the series updates once per session (radon-cor.timer runs
 * fetch_cor.py directly), so there is no manual-scan POST. Reads through the
 * dbFirstRead chokepoint — Turso scan_snapshots row (service='cor') first,
 * last-in-memory fallback — and ALWAYS returns 200; absent data is the
 * contract's `missing: true` shape, never a 4xx.
 *
 * Mechanics mirror straddle-api.test.ts: @/lib/db backed by a real in-memory
 * libsql client seeded with the scan_snapshots schema; fs mocked.
 * Spec: docs/indicators/cor.md.
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
    source_last_modified: {
      cor1m: "Fri, 07 Aug 2026 22:31:04 GMT",
      cor3m: "Fri, 07 Aug 2026 22:31:10 GMT",
      cor6m: "Fri, 07 Aug 2026 22:31:16 GMT",
      cor1y: "Fri, 07 Aug 2026 22:31:22 GMT",
    },
    count: 5181,
    current: {
      date: "2026-08-07",
      cor1m: 7.38,
      cor3m: 10.48,
      cor6m: 12.16,
      cor1y: 14.19,
      term_spread: 6.81,
      change_1d: { cor1m: 0.76, cor3m: 1.07, cor6m: 0.98, cor1y: 0.74 },
    },
    stats: {
      cor1m: { high: 96.59, low: 2.93, avg: 37.198579, stddev: 17.867378, percentile: 0.010037 },
      cor3m: { high: 90.79, low: 7.19, avg: 41.240333, stddev: 16.35408, percentile: 0.012582 },
      cor6m: { high: 86.35, low: 9.67, avg: 44.342579, stddev: 15.781472, percentile: 0.0083 },
      cor1y: { high: 79.77, low: 11.9, avg: 46.133582, stddev: 14.713988, percentile: 0.007721 },
    },
    series: [
      { date: "2026-08-06", cor1m: 6.62, cor3m: 9.41, cor6m: 11.18, cor1y: 13.45 },
      { date: "2026-08-07", cor1m: 7.38, cor3m: 10.48, cor6m: 12.16, cor1y: 14.19 },
    ],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('cor', ?, ?)`,
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

describe("GET /api/cor", () => {
  it("serves the latest Turso snapshot (newest row wins)", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: staleTime, count: 1 }));

    const { GET } = await import("../app/api/cor/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(5181);
    expect((json.current as Payload).cor6m).toBe(12.16);
    expect(json.missing).toBeUndefined();
  });

  it("serves the last good read from memory when Turso has no rows", async () => {
    await insertSnapshot(buildPayload({ count: 5180 }));

    const { GET } = await import("../app/api/cor/route");
    await GET();
    await db.execute("DELETE FROM scan_snapshots");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(5180);
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/cor/route");
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

  it("only reads the cor service's snapshots", async () => {
    await db.execute({
      sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('straddle', ?, ?)`,
      args: [new Date().toISOString(), JSON.stringify(buildPayload({ count: 5 }))],
    });

    const { GET } = await import("../app/api/cor/route");
    const res = await GET();
    const json = await jsonOf(res);
    expect(json.missing).toBe(true);
  });

  it("declares force-dynamic per the snapshot route cache contract", async () => {
    const route = await import("../app/api/cor/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
