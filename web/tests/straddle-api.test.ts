/**
 * @vitest-environment node
 *
 * /api/straddle — SPX realized vs implied 1-day straddle ratio route (regime tab).
 *
 * GET-only: the series updates once per session (radon-straddle.timer runs
 * fetch_straddle.py directly), so there is no manual-scan POST. Reads through
 * the dbFirstRead chokepoint — Turso scan_snapshots row (service='straddle')
 * first, disk data/straddle.json fallback — and ALWAYS returns 200; absent
 * data is the contract's `missing: true` shape, never a 4xx.
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
    source_last_modified: {
      spx: "Wed, 05 Aug 2026 17:01:43 GMT",
      vix1d: "Wed, 05 Aug 2026 18:31:25 GMT",
    },
    count: 1059,
    current: {
      date: "2026-08-04",
      ratio: 3.775801,
      move_pct: 1.789619,
      implied_straddle_pct: 0.473971,
      spx_close: 7736.52,
      vix1d_prior: 9.43,
    },
    stats: { high: 3.775801, low: -4.968372, avg: 0.063255, stddev: 1.157428, hit_rate: 0.367675 },
    series: [
      { date: "2026-08-03", spx_close: 7600.5, vix1d_close: 9.43, ratio: -0.42 },
      { date: "2026-08-04", spx_close: 7736.52, vix1d_close: 13.86, ratio: 3.775801 },
    ],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('straddle', ?, ?)`,
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

describe("GET /api/straddle", () => {
  it("serves the latest Turso snapshot (Turso-first over an older disk cache)", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(
      JSON.stringify(buildPayload({ scan_time: staleTime, count: 1 })),
    );

    const { GET } = await import("../app/api/straddle/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(1059);
    expect((json.current as Payload).ratio).toBe(3.775801);
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload({ count: 1058 })));

    const { GET } = await import("../app/api/straddle/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(1058);
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/straddle/route");
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

  it("only reads the straddle service's snapshots", async () => {
    await db.execute({
      sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('rv-ratio', ?, ?)`,
      args: [new Date().toISOString(), JSON.stringify(buildPayload({ count: 5 }))],
    });

    const { GET } = await import("../app/api/straddle/route");
    const res = await GET();
    const json = await jsonOf(res);
    expect(json.missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/straddle/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
