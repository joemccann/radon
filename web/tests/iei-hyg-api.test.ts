/**
 * @vitest-environment node
 *
 * /api/iei-hyg — IEI/HYG ratio with 52-week extremes (regime tab).
 *
 * GET-only: the series updates daily (radon-iei-hyg timer runs
 * fetch_iei_hyg.py directly). Reads through the dbFirstRead chokepoint —
 * Turso scan_snapshots row (service='iei-hyg') first, disk data/iei_hyg.json
 * fallback — and ALWAYS returns 200; absent data is the contract's
 * `missing: true` shape, never a 4xx.
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
    source: "yahoo",
    count: 62,
    current: {
      date: "2026-08-21",
      iei_close: 116.41000366210938,
      hyg_close: 79.61000061035156,
      dxy_close: 98.80000305175781,
      ratio: 1.462253520532856,
      ratio_52w_low: 1.462253520532856,
      low_date: "2026-08-21",
      ratio_52w_high: 1.475760927676247,
      high_date: "2026-06-26",
      ratio_pct_rank: 0,
      window_sessions: 62,
      state: "new_low",
    },
    series: [
      { date: "2026-08-20", iei_close: 116.6, hyg_close: 79.6, dxy_close: null, ratio: 1.464932210008201 },
      { date: "2026-08-21", iei_close: 116.41000366210938, hyg_close: 79.61000061035156, dxy_close: 98.80000305175781, ratio: 1.462253520532856 },
    ],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('iei-hyg', ?, ?)`,
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

describe("GET /api/iei-hyg", () => {
  it("serves the latest Turso snapshot (Turso-first over an older disk cache)", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload({ scan_time: staleTime, count: 1 })));

    const { GET } = await import("../app/api/iei-hyg/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(62);
    expect((json.current as Payload).state).toBe("new_low");
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload({ count: 61 })));

    const { GET } = await import("../app/api/iei-hyg/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect((await jsonOf(res)).count).toBe(61);
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/iei-hyg/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      missing: true,
      scan_time: null,
      source: null,
      count: 0,
      current: null,
      series: [],
    });
  });

  it("only reads the iei-hyg service's snapshots", async () => {
    await db.execute({
      sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('credit-spread', ?, ?)`,
      args: [new Date().toISOString(), JSON.stringify(buildPayload({ count: 5 }))],
    });

    const { GET } = await import("../app/api/iei-hyg/route");
    const json = await jsonOf(await GET());
    expect(json.missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/iei-hyg/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
