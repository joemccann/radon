/**
 * @vitest-environment node
 *
 * /api/calm-streak: consecutive SPX sessions without a >1% intraday band
 * (regime tab CALM STREAK). GET-only; dbFirstRead over scan_snapshots
 * (service='calm-streak') with data/calm_streak.json fallback. Always 200.
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

type Payload = Record<string, unknown>;

const DAY_MS = 86_400_000;
const DATA_DATE = new Date(Date.now() - DAY_MS).toISOString().slice(0, 10);

function buildPayload(overrides: Payload = {}): Payload {
  return {
    schema_version: 1,
    scan_time: new Date().toISOString(),
    data_date: DATA_DATE,
    source_last_modified: "Tue, 15 Sep 2026 13:02:41 GMT",
    source: { name: "cboe", url: "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_SPX.json" },
    threshold_pct: 1.0,
    current: { date: DATA_DATE, streak: 28, band_pct: 0.7312, close: 7619.98 },
    stats: {
      max: { streak: 64, date: "2017-03-20" },
      window: { start: "1996-01-01", end: "2016-12-31", streak: 26, date: "2014-06-23" },
      percentile: 98.9,
    },
    series: [{ date: DATA_DATE, streak: 28, close: 7619.98 }],
    missing: false,
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, service = "calm-streak"): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES (?, ?, ?)`,
    args: [service, payload.scan_time as string, JSON.stringify(payload)],
  });
}

const ENOENT = Object.assign(new Error("ENOENT: no such file"), { code: "ENOENT" });
const jsonOf = async (res: Response) => (await res.json()) as Payload;

beforeEach(async () => {
  vi.resetModules();
  db = createClient({ url: ":memory:" });
  await db.execute(`CREATE TABLE scan_snapshots (
    service TEXT NOT NULL, scan_time TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (service, scan_time))`);
  mockGetDb.mockReturnValue(db);
  mockReadFile.mockRejectedValue(ENOENT);
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  db.close();
  vi.restoreAllMocks();
});

describe("GET /api/calm-streak", () => {
  it("serves the latest Turso snapshot over an older disk cache", async () => {
    await insertSnapshot(buildPayload());
    const older = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(
      JSON.stringify(buildPayload({ scan_time: older, current: { date: DATA_DATE, streak: 1, band_pct: 2, close: 1 } })),
    );
    const { GET } = await import("../app/api/calm-streak/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect((json.current as Payload).streak).toBe(28);
    expect(((json.stats as Payload).window as Payload).streak).toBe(26);
    expect(json.missing).toBe(false);
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload()));
    const { GET } = await import("../app/api/calm-streak/route");
    const json = await jsonOf(await GET());
    expect(json.data_date).toBe(DATA_DATE);
    expect(mockReadFile.mock.calls[0][0]).toMatch(/data[\\/]calm_streak\.json$/);
  });

  it("returns the exact missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/calm-streak/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      missing: true,
      scan_time: null,
      data_date: null,
      current: null,
      stats: null,
      series: [],
    });
  });

  it("only reads the calm-streak service (no cross-service leak)", async () => {
    await insertSnapshot(buildPayload(), "ma-ratio");
    const { GET } = await import("../app/api/calm-streak/route");
    expect((await jsonOf(await GET())).missing).toBe(true);
  });

  it("collapses a snapshot past 48h to missing+stale, keeping its scan_time", async () => {
    const deadAge = new Date(Date.now() - 72 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: deadAge }));
    const { GET } = await import("../app/api/calm-streak/route");
    const json = await jsonOf(await GET());
    expect(json.missing).toBe(true);
    expect(json.stale).toBe(true);
    expect(json.scan_time).toBe(deadAge);
    expect(json.series).toEqual([]);
  });

  it("declares force-dynamic, nodejs runtime and the read capability", async () => {
    const route = await import("../app/api/calm-streak/route");
    expect(route.dynamic).toBe("force-dynamic");
    expect(route.runtime).toBe("nodejs");
    expect(route.radonCapability).toBe("read");
  });
});
