/**
 * @vitest-environment node
 *
 * /api/hyad — FINRA TRACE high yield bond cumulative advance-decline line
 * (regime tab HY AD).
 *
 * GET-only (radon-hyad timer runs the pull Tue..Sat mornings). Reads through
 * dbFirstRead — Turso scan_snapshots (service='hy-ad') first, disk
 * data/hyad.json fallback — and ALWAYS returns 200; absent data is the
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

// Window-relative dates: the data date is always "yesterday", never hardcoded.
const DAY_MS = 86_400_000;
const DATA_DATE = new Date(Date.now() - DAY_MS).toISOString().slice(0, 10);
const DISK_DATA_DATE = new Date(Date.now() - 3 * DAY_MS).toISOString().slice(0, 10);

function seriesPoint(dateStr: string, cum: number): Payload {
  return { date: dateStr, net: -277, cum, ma21: null, ma50: null, spx_close: 6411.37 };
}

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    data_date: DATA_DATE,
    current: {
      date: DATA_DATE,
      advances: 1227,
      declines: 1504,
      unchanged: 69,
      total: 3163,
      net: -277,
      cum: -2535,
      ma21: -1010.4,
      ma50: 850.2,
    },
    series: [seriesPoint(DISK_DATA_DATE, -2258), seriesPoint(DATA_DATE, -2535)],
    ...overrides,
  };
}

function diskPayload(scanTime?: string): Payload {
  return buildPayload({
    ...(scanTime ? { scan_time: scanTime } : {}),
    data_date: DISK_DATA_DATE,
    series: [seriesPoint(DISK_DATA_DATE, -2258)],
  });
}

async function insertSnapshot(payload: Payload, service = "hy-ad"): Promise<void> {
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

describe("GET /api/hyad", () => {
  it("serves the latest Turso snapshot over an older disk cache", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload(staleTime)));
    const { GET } = await import("../app/api/hyad/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.data_date).toBe(DATA_DATE);
    expect((json.current as Payload).cum).toBe(-2535);
    expect((json.current as Payload).net).toBe(-277);
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload()));
    const { GET } = await import("../app/api/hyad/route");
    const json = await jsonOf(await GET());
    expect(json.data_date).toBe(DISK_DATA_DATE);
    expect((json.series as Payload[]).length).toBe(1);
    expect(json.missing).toBeUndefined();
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/hyad/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      missing: true,
      scan_time: null,
      data_date: null,
      current: null,
      series: [],
    });
  });

  it("only reads the hy-ad service's snapshots (no cross-service leak)", async () => {
    await insertSnapshot(buildPayload(), "credit-spread");
    const { GET } = await import("../app/api/hyad/route");
    expect((await jsonOf(await GET())).missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/hyad/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
