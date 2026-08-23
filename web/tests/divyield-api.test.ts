/**
 * @vitest-environment node
 *
 * /api/divyield — percent of S&P 500 stocks with trailing dividend yield above
 * the 10-Year Treasury yield (regime tab DIV YIELD).
 *
 * GET-only (radon-divyield timer runs the sweep once daily at 22:40 UTC).
 * Reads through dbFirstRead — Turso scan_snapshots (service='div-yield')
 * first, disk data/divyield.json fallback — and ALWAYS returns 200; absent
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

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    source: { constituents: "github-datasets", constituents_count: 503, quote_errors: 0 },
    data_date: DATA_DATE,
    y10_date: DATA_DATE,
    current: {
      date: DATA_DATE,
      pct_above: 3.78,
      count_above: 19,
      total: 503,
      y10: 4.74,
      leaders: [{ ticker: "TDG", yield_pct: 7.5 }],
    },
    series: [
      { date: "1990-01-31", pct_above: 12.1, count_above: 58, total: 480, y10: 8.21, approximate: 1 },
      { date: DATA_DATE, pct_above: 3.78, count_above: 19, total: 503, y10: 4.74, approximate: 0 },
    ],
    backfill_cutover: DATA_DATE,
    ...overrides,
  };
}

function diskPayload(scanTime?: string): Payload {
  return buildPayload({
    ...(scanTime ? { scan_time: scanTime } : {}),
    source: { constituents: "disk-cache", constituents_count: 503, quote_errors: 0 },
  });
}

async function insertSnapshot(payload: Payload, service = "div-yield"): Promise<void> {
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

describe("GET /api/divyield", () => {
  it("serves the latest Turso snapshot over an older disk cache", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload(staleTime)));
    const { GET } = await import("../app/api/divyield/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect((json.source as Payload).constituents).toBe("github-datasets");
    expect((json.current as Payload).count_above).toBe(19);
    expect((json.current as Payload).pct_above).toBe(3.78);
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload()));
    const { GET } = await import("../app/api/divyield/route");
    const json = await jsonOf(await GET());
    expect((json.source as Payload).constituents).toBe("disk-cache");
    expect(json.data_date).toBe(DATA_DATE);
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/divyield/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      missing: true,
      scan_time: null,
      data_date: null,
      current: null,
      series: [],
      backfill_cutover: null,
    });
  });

  it("only reads the div-yield service's snapshots (no cross-service leak)", async () => {
    await insertSnapshot(buildPayload(), "yield-curve");
    const { GET } = await import("../app/api/divyield/route");
    expect((await jsonOf(await GET())).missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/divyield/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
