/** @vitest-environment node */
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

function seriesPoint(dateStr: string, net: number): Payload {
  return { date: dateStr, spx_close: 7650.5, above_200_pct: 49.5, new_highs: 5, new_lows: 29, net_high_low_per_500: net };
}
function buildPayload(overrides: Payload = {}): Payload {
  return { scan_time: new Date().toISOString(), data_date: DATA_DATE,
    current: seriesPoint(DATA_DATE, -4.8), series: [seriesPoint(DISK_DATA_DATE, -4.2), seriesPoint(DATA_DATE, -4.8)], ...overrides };
}

function diskPayload(scanTime?: string): Payload {
  return buildPayload({
    ...(scanTime ? { scan_time: scanTime } : {}),
    data_date: DISK_DATA_DATE,
    series: [seriesPoint(DISK_DATA_DATE, -4.2)],
  });
}

async function insertSnapshot(payload: Payload, service = "record-high-breadth"): Promise<void> {
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

describe("GET /api/record-high-breadth", () => {
  it("serves the latest Turso snapshot over an older disk cache", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload(staleTime)));
    const { GET } = await import("../app/api/record-high-breadth/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    const json = await jsonOf(res);
    expect(json.data_date).toBe(DATA_DATE);
    expect((json.current as Payload).net_high_low_per_500).toBe(-4.8);
    expect((json.current as Payload).new_highs).toBe(5);
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload()));
    const { GET } = await import("../app/api/record-high-breadth/route");
    const json = await jsonOf(await GET());
    expect(json.data_date).toBe(DISK_DATA_DATE);
    expect((json.series as Payload[]).length).toBe(1);
    expect(json.missing).toBeUndefined();
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/record-high-breadth/route");
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

  it("only reads the record-high-breadth service's snapshots (no cross-service leak)", async () => {
    await insertSnapshot(buildPayload(), "credit-spread");
    const { GET } = await import("../app/api/record-high-breadth/route");
    expect((await jsonOf(await GET())).missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/record-high-breadth/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
