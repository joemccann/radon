/**
 * @vitest-environment node
 *
 * /api/rsi-oversold — SPX percent of members with Wilder RSI(14) below 30
 * (regime tab RSI OVERSOLD).
 *
 * GET-only (radon-rsi-oversold timer runs the sweep once daily at 23:05 UTC).
 * Reads through dbFirstRead — Turso scan_snapshots (service='rsi-oversold')
 * first, disk data/rsi_oversold.json fallback — and ALWAYS returns 200; absent
 * data is the contract's `missing: true` shape, never a 4xx.
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

const DAY_MS = 86_400_000;
const DATA_DATE = new Date(Date.now() - DAY_MS).toISOString().slice(0, 10);
const PRIOR_DATE = new Date(Date.now() - 2 * DAY_MS).toISOString().slice(0, 10);

function buildPayload(overrides: Payload = {}): Payload {
  return {
    schema_version: 1,
    scan_time: new Date().toISOString(),
    data_date: DATA_DATE,
    source: {
      constituents: "cache",
      constituents_count: 503,
      member_close_fetches: { yahoo: 490, stored: 13 },
    },
    threshold: 10.0,
    current: {
      date: DATA_DATE,
      pct_below_30: 12.4,
      count_below_30: 62,
      eligible: 500,
      spx_close: 6630.0,
      state: "OVERSOLD CLUSTER",
      cross_up: false,
      highest_since: "2026-03-13",
    },
    series: [
      { date: PRIOR_DATE, pct_below_30: 8.1, count_below_30: 40, eligible: 498, spx_close: 6600.12 },
      { date: DATA_DATE, pct_below_30: 12.4, count_below_30: 62, eligible: 500, spx_close: 6630.0 },
    ],
    missing: false,
    ...overrides,
  };
}

function diskPayload(scanTime?: string): Payload {
  return buildPayload({
    ...(scanTime ? { scan_time: scanTime } : {}),
    source: { constituents: "disk-cache", constituents_count: 503, member_close_fetches: null },
  });
}

async function insertSnapshot(payload: Payload, service = "rsi-oversold"): Promise<void> {
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

describe("GET /api/rsi-oversold", () => {
  it("serves the latest Turso snapshot over an older disk cache", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload(staleTime)));
    const { GET } = await import("../app/api/rsi-oversold/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect((json.source as Payload).constituents).toBe("cache");
    expect((json.current as Payload).pct_below_30).toBe(12.4);
    expect(json.threshold).toBe(10.0);
    expect(json.missing).toBe(false);
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload()));
    const { GET } = await import("../app/api/rsi-oversold/route");
    const json = await jsonOf(await GET());
    expect((json.source as Payload).constituents).toBe("disk-cache");
    expect(json.data_date).toBe(DATA_DATE);
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/rsi-oversold/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      missing: true,
      scan_time: null,
      data_date: null,
      current: null,
      series: [],
      threshold: null,
    });
  });

  it("only reads the rsi-oversold service's snapshots (no cross-service leak)", async () => {
    await insertSnapshot(buildPayload(), "ma-ratio");
    const { GET } = await import("../app/api/rsi-oversold/route");
    expect((await jsonOf(await GET())).missing).toBe(true);
  });

  it("collapses a snapshot past 48h to missing+stale, keeping its scan_time", async () => {
    const deadAge = new Date(Date.now() - 72 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: deadAge }));

    const { GET } = await import("../app/api/rsi-oversold/route");
    const json = await jsonOf(await GET());
    expect(json.missing).toBe(true);
    expect(json.stale).toBe(true);
    expect(json.scan_time).toBe(deadAge);
    expect(json.series).toEqual([]);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/rsi-oversold/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
