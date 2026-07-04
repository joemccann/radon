/**
 * @vitest-environment node
 *
 * /api/margin-debt — FINRA margin debt series route (regime tab).
 *
 * GET-only: the series updates monthly (radon-margin-debt-refresh timer runs
 * fetch_margin_debt.py directly), so there is no manual-scan POST. Reads
 * through the dbFirstRead chokepoint — Turso scan_snapshots row
 * (service='margin-debt', the full payload with display/normalized views)
 * first, disk data/margin_debt.json fallback — and ALWAYS returns 200;
 * absent data is the contract's `missing: true` shape, never a 4xx.
 *
 * Mechanics mirror breadth-api.test.ts: @/lib/db backed by a real in-memory
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
    source_last_modified: "Tue, 16 Jun 2026 14:52:10 GMT",
    count: 809,
    splice: { legacy_source: "nyse_legacy", ratio: 1.039, first_finra_month: "1997-01" },
    normalization: { available: true },
    current: { date: "2026-05", level: 1415557, level_yoy_pct: 53.7 },
    series: [
      { date: "2026-04", level: 1304281, source: "finra" },
      { date: "2026-05", level: 1415557, source: "finra" },
    ],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('margin-debt', ?, ?)`,
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

describe("GET /api/margin-debt", () => {
  it("serves the latest Turso snapshot (Turso-first over an older disk cache)", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(
      JSON.stringify(buildPayload({ scan_time: staleTime, count: 1 })),
    );

    const { GET } = await import("../app/api/margin-debt/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(809);
    expect((json.current as Payload).level).toBe(1415557);
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload({ count: 808 })));

    const { GET } = await import("../app/api/margin-debt/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(808);
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/margin-debt/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json).toEqual({
      missing: true,
      scan_time: null,
      count: 0,
      series: [],
      current: null,
      splice: null,
      normalization: null,
    });
  });

  it("only reads the margin-debt service's snapshots", async () => {
    await db.execute({
      sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('leap-scan', ?, ?)`,
      args: [new Date().toISOString(), JSON.stringify(buildPayload({ count: 5 }))],
    });

    const { GET } = await import("../app/api/margin-debt/route");
    const res = await GET();
    const json = await jsonOf(res);
    expect(json.missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/margin-debt/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
