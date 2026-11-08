/** @vitest-environment node */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: mockGetDb }));

async function seedSchema(client: Client): Promise<void> {
  await client.execute(`CREATE TABLE scan_snapshots (
    service TEXT NOT NULL, scan_time TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (service, scan_time))`);
}

type Payload = Record<string, unknown>;

const DAY_MS = 86_400_000;
const DATA_DATE = new Date(Date.now() - DAY_MS).toISOString().slice(0, 10);
const DISK_DATA_DATE = new Date(Date.now() - 3 * DAY_MS).toISOString().slice(0, 10);

function seriesPoint(dateStr: string, imbalance: number): Payload {
  return {
    date: dateStr, ask: 100 + imbalance, bid: 100, batd: imbalance,
    imbalance, ema10: imbalance, spx_close: 7376.86,
  };
}
function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(), data_date: DATA_DATE,
    current: seriesPoint(DATA_DATE, 908_514),
    series: [seriesPoint(DISK_DATA_DATE, 60), seriesPoint(DATA_DATE, 908_514)],
    source: { provider: "tradingview", units: "trade_count", units_verified: true, history_complete: false },
    ...overrides,
  };
}

function olderPayload(scanTime?: string): Payload {
  return buildPayload({
    ...(scanTime ? { scan_time: scanTime } : {}),
    data_date: DISK_DATA_DATE,
    series: [seriesPoint(DISK_DATA_DATE, 60)],
  });
}

async function insertSnapshot(payload: Payload, service = "imbalance"): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES (?, ?, ?)`,
    args: [service, payload.scan_time as string, JSON.stringify(payload)],
  });
}

const jsonOf = async (res: Response) => (await res.json()) as Record<string, unknown>;

beforeEach(async () => {
  vi.resetModules();
  db = createClient({ url: ":memory:" });
  await seedSchema(db);
  mockGetDb.mockReturnValue(db);
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  db.close();
  vi.restoreAllMocks();
});

describe("GET /api/imbalance", () => {
  it("serves the latest Turso snapshot over an older row", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    await insertSnapshot(olderPayload(staleTime));
    const { GET } = await import("../app/api/imbalance/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    const json = await jsonOf(res);
    expect(json.data_date).toBe(DATA_DATE);
    expect((json.current as Payload).imbalance).toBe(908_514);
    expect((json.current as Payload).ask).toBe(100 + 908_514);
    expect(json.missing).toBeUndefined();
  });

  it("serves the last good read from memory when Turso has no rows", async () => {
    await insertSnapshot(olderPayload());
    const { GET } = await import("../app/api/imbalance/route");
    await GET();
    await db.execute("DELETE FROM scan_snapshots");
    const json = await jsonOf(await GET());
    expect(json.data_date).toBe(DISK_DATA_DATE);
    expect((json.series as Payload[]).length).toBe(1);
    expect(json.missing).toBeUndefined();
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/imbalance/route");
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

  it("only reads the imbalance service's snapshots (no cross-service leak)", async () => {
    await insertSnapshot(buildPayload(), "record-high-breadth");
    const { GET } = await import("../app/api/imbalance/route");
    expect((await jsonOf(await GET())).missing).toBe(true);
  });

  it("declares force-dynamic per the snapshot route cache contract", async () => {
    const route = await import("../app/api/imbalance/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
