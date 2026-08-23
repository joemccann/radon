/**
 * @vitest-environment node
 *
 * /api/trin — NYSE Arms Index, 60-minute bars + MA(10) (regime tab).
 *
 * GET-only (radon-trin timer samples IB every 5 minutes during RTH). Reads
 * through dbFirstRead — Turso scan_snapshots (service='trin') first, disk
 * data/trin.json fallback — and ALWAYS returns 200; absent data is the
 * contract's `missing: true` shape, never a 4xx.
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

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    source: "ib+stockcharts",
    current: {
      ts: "2026-08-21T19:55:00Z", session_date: "2026-08-21",
      trin: 0.68, ma10: 0.61, state: "near_zone",
      adv: 1510, dec: 1280, up_vol: 1.9e9, down_vol: 1.4e9,
      daily_close: 0.68, daily_date: "2026-08-21",
      zone_low: 0.6, zone_near: 0.65, zone_high: 1.5,
    },
    hourly: [
      { ts: "2026-08-21T18:55:00Z", bucket: "2026-08-21T14:30", trin: 0.72, ma10: 0.63 },
      { ts: "2026-08-21T19:55:00Z", bucket: "2026-08-21T15:30", trin: 0.68, ma10: 0.61 },
    ],
    daily: [{ date: "2026-08-20", close: 0.77 }, { date: "2026-08-21", close: 0.68 }],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('trin', ?, ?)`,
    args: [scanTime ?? (payload.scan_time as string), JSON.stringify(payload)],
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

describe("GET /api/trin", () => {
  it("serves the latest Turso snapshot over an older disk cache", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload({ scan_time: staleTime, source: "disk" })));
    const { GET } = await import("../app/api/trin/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.source).toBe("ib+stockcharts");
    expect((json.current as Payload).state).toBe("near_zone");
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload({ source: "disk" })));
    const { GET } = await import("../app/api/trin/route");
    expect((await jsonOf(await GET())).source).toBe("disk");
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/trin/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      missing: true, scan_time: null, source: null, current: null, hourly: [], daily: [],
    });
  });

  it("only reads the trin service's snapshots", async () => {
    await db.execute({
      sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('breadth-scan', ?, ?)`,
      args: [new Date().toISOString(), JSON.stringify(buildPayload())],
    });
    const { GET } = await import("../app/api/trin/route");
    expect((await jsonOf(await GET())).missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/trin/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
