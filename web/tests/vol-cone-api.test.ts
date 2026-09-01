/**
 * @vitest-environment node
 *
 * /api/vol-cone — cheap 10% OTM wing IV scanner route (scanner tab).
 *
 * GET-only: radon-vol-cone.timer runs fetch_vol_cone.py. Reads through
 * dbFirstRead (scan_snapshots service='vol-cone', disk data/vol_cone.json)
 * and ALWAYS returns 200; absent data is missing:true, never a 4xx.
 * Spec: docs/indicators/vol-cone.md.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: mockGetDb,
}));

const mockReadFile = vi.fn();
vi.mock("fs/promises", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs/promises")>();
  return {
    ...actual,
    readFile: (...args: unknown[]) => mockReadFile(...args),
  };
});

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
    source_as_of: "2026-08-12",
    count: 2,
    hit_count: 1,
    current: {
      ticker: "NVDA",
      expiry: "2026-09-18",
      dte: 37,
      atm_iv: 0.3851329156797111,
      call_10_iv: 0.3862120615005326,
      put_10_iv: 0.39731998999142565,
      atm_percentile: 0,
      wing_score: 0.08333333333333333,
      regime: "CHEAP_WINGS",
    },
    names: [
      { ticker: "NVDA", regime: "CHEAP_WINGS", wing_score: 0.0833, series: [] },
      { ticker: "SMH", regime: "NEUTRAL", wing_score: 0.4, series: [] },
    ],
    hits: [{ ticker: "NVDA", regime: "CHEAP_WINGS", wing_score: 0.0833, series: [] }],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('vol-cone', ?, ?)`,
    args: [scanTime ?? (payload.scan_time as string), JSON.stringify(payload)],
  });
}

const ENOENT = Object.assign(new Error("ENOENT: no such file"), { code: "ENOENT" });

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  return (await res.json()) as Record<string, unknown>;
}

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

describe("GET /api/vol-cone", () => {
  it("serves the latest Turso snapshot (Turso-first over an older disk cache)", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(
      JSON.stringify(buildPayload({ scan_time: staleTime, count: 1 })),
    );

    const { GET } = await import("../app/api/vol-cone/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(2);
    expect((json.current as Payload).ticker).toBe("NVDA");
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload({ count: 3 })));

    const { GET } = await import("../app/api/vol-cone/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(3);
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/vol-cone/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json).toEqual({
      missing: true,
      scan_time: null,
      source_as_of: null,
      count: 0,
      hit_count: 0,
      current: null,
      names: [],
      hits: [],
    });
  });

  // The writers are Mon-Fri only (intraday RTH + EOD 20:45 UTC), so the
  // longest healthy quiet period is Fri 20:45 UTC -> Mon 20:45 UTC + jitter
  // on a holiday Monday (~73h). A 48h budget collapsed Friday's healthy
  // snapshot into the "outage, not an empty result" banner every Sunday
  // evening (screenshot 2026-08-31 02:26 UTC, snapshot 2026-08-28 20:45 UTC).
  it("serves a weekend-aged snapshot (60h) instead of collapsing to missing", async () => {
    const sundayAge = new Date(Date.now() - 60 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: sundayAge }), sundayAge);

    const { GET } = await import("../app/api/vol-cone/route");
    const json = await jsonOf(await GET());
    expect(json.missing).toBeUndefined();
    expect(json.count).toBe(2);
  });

  it("still collapses a snapshot past the closed window, keeping its scan_time", async () => {
    const deadAge = new Date(Date.now() - 5 * 24 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: deadAge }), deadAge);

    const { GET } = await import("../app/api/vol-cone/route");
    const json = await jsonOf(await GET());
    expect(json.missing).toBe(true);
    expect(json.stale).toBe(true);
    expect(json.scan_time).toBe(deadAge);
    expect(json.hits).toEqual([]);
  });

  it("only reads the vol-cone service's snapshots", async () => {
    await db.execute({
      sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('skew', ?, ?)`,
      args: [new Date().toISOString(), JSON.stringify(buildPayload({ count: 5 }))],
    });

    const { GET } = await import("../app/api/vol-cone/route");
    const res = await GET();
    const json = await jsonOf(res);
    expect(json.missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/vol-cone/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
