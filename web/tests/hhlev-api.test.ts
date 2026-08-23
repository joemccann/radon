/**
 * @vitest-environment node
 *
 * /api/hhlev — US household leverage, percent of net worth (Z.1 B.101 family,
 * regime tab HH LEV).
 *
 * GET-only (radon-hhlev timer runs the daily pull of the quarterly source).
 * Reads through dbFirstRead — Turso scan_snapshots (service='hhlev') first,
 * disk data/hhlev.json fallback — and ALWAYS returns 200; absent data is the
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

// Window-relative dates: quarter starts derived from today, never hardcoded.
function quarterStart(quartersBack: number): string {
  const now = new Date();
  const total = now.getUTCFullYear() * 4 + Math.floor(now.getUTCMonth() / 3) - quartersBack;
  const year = Math.floor(total / 4);
  const month = (total % 4) * 3 + 1;
  return `${year}-${String(month).padStart(2, "0")}-01`;
}

const DATA_DATE = quarterStart(1);
const DISK_DATA_DATE = quarterStart(2);

function seriesPoint(dateStr: string, leveragePct: number): Payload {
  return { date: dateStr, leverage_pct: leveragePct };
}

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    source_last_modified: DATA_DATE,
    data_date: DATA_DATE,
    current: {
      date: DATA_DATE,
      leverage_pct: 11.78,
      liabilities_musd: 21560050,
      net_worth_musd: 182979889,
    },
    series: [seriesPoint(DISK_DATA_DATE, 11.81), seriesPoint(DATA_DATE, 11.78)],
    ...overrides,
  };
}

function diskPayload(scanTime?: string): Payload {
  return buildPayload({
    ...(scanTime ? { scan_time: scanTime } : {}),
    data_date: DISK_DATA_DATE,
    current: {
      date: DISK_DATA_DATE,
      leverage_pct: 11.81,
      liabilities_musd: 21506681,
      net_worth_musd: 182866812,
    },
    series: [seriesPoint(DISK_DATA_DATE, 11.81)],
  });
}

async function insertSnapshot(payload: Payload, service = "hhlev"): Promise<void> {
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

describe("GET /api/hhlev", () => {
  it("serves the latest Turso snapshot over an older disk cache", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload(staleTime)));
    const { GET } = await import("../app/api/hhlev/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.data_date).toBe(DATA_DATE);
    expect((json.current as Payload).leverage_pct).toBe(11.78);
    expect((json.current as Payload).liabilities_musd).toBe(21560050);
    expect((json.current as Payload).net_worth_musd).toBe(182979889);
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload()));
    const { GET } = await import("../app/api/hhlev/route");
    const json = await jsonOf(await GET());
    expect(json.data_date).toBe(DISK_DATA_DATE);
    expect((json.series as Payload[]).length).toBe(1);
    expect(json.missing).toBeUndefined();
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/hhlev/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      missing: true,
      scan_time: null,
      source_last_modified: null,
      data_date: null,
      current: null,
      series: [],
    });
  });

  it("only reads the hhlev service's snapshots (no cross-service leak)", async () => {
    await insertSnapshot(buildPayload(), "margin-debt");
    const { GET } = await import("../app/api/hhlev/route");
    expect((await jsonOf(await GET())).missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/hhlev/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
