/**
 * @vitest-environment node
 *
 * /api/ma-ratio — SPX percent above 50d MA over percent above 200d MA
 * (regime tab MA RATIO).
 *
 * GET-only (radon-ma-ratio timer runs the sweep once daily at 22:45 UTC).
 * Reads through dbFirstRead — Turso scan_snapshots (service='ma-ratio')
 * first, disk data/ma_ratio.json fallback — and ALWAYS returns 200; absent
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

// Window-relative dates: the data date is always "yesterday", never hardcoded.
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
    zone: { low: 0.25, high: 0.5 },
    current: {
      date: DATA_DATE,
      pct_above_50: 46.5,
      pct_above_200: 64.6,
      ratio: 0.72,
      count_above_50: 234,
      count_above_200: 325,
      eligible_50: 503,
      eligible_200: 503,
      spx_close: 7631.47,
    },
    series: [
      { date: PRIOR_DATE, pct_above_50: 48.1, pct_above_200: 65.0, ratio: 0.74, spx_close: 7600.12 },
      { date: DATA_DATE, pct_above_50: 46.5, pct_above_200: 64.6, ratio: 0.72, spx_close: 7631.47 },
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

async function insertSnapshot(payload: Payload, service = "ma-ratio"): Promise<void> {
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

describe("GET /api/ma-ratio", () => {
  it("serves the latest Turso snapshot over an older disk cache", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload(staleTime)));
    const { GET } = await import("../app/api/ma-ratio/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect((json.source as Payload).constituents).toBe("cache");
    expect((json.current as Payload).ratio).toBe(0.72);
    expect((json.zone as Payload).low).toBe(0.25);
    expect((json.zone as Payload).high).toBe(0.5);
    expect(json.missing).toBe(false);
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload()));
    const { GET } = await import("../app/api/ma-ratio/route");
    const json = await jsonOf(await GET());
    expect((json.source as Payload).constituents).toBe("disk-cache");
    expect(json.data_date).toBe(DATA_DATE);
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/ma-ratio/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      missing: true,
      scan_time: null,
      data_date: null,
      current: null,
      series: [],
      zone: null,
    });
  });

  it("preserves a null ratio row through the payload (zero-denominator guard)", async () => {
    const washed = buildPayload({
      series: [
        { date: PRIOR_DATE, pct_above_50: 0, pct_above_200: 0, ratio: null, spx_close: 5100.0 },
        { date: DATA_DATE, pct_above_50: 2.4, pct_above_200: 1.2, ratio: 2.0, spx_close: 5200.0 },
      ],
    });
    await insertSnapshot(washed);
    const { GET } = await import("../app/api/ma-ratio/route");
    const json = await jsonOf(await GET());
    const series = json.series as Payload[];
    expect(series[0].ratio).toBeNull();
  });

  it("only reads the ma-ratio service's snapshots (no cross-service leak)", async () => {
    await insertSnapshot(buildPayload(), "div-yield");
    const { GET } = await import("../app/api/ma-ratio/route");
    expect((await jsonOf(await GET())).missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/ma-ratio/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
