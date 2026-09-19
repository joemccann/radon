/**
 * @vitest-environment node
 *
 * /api/panic-index — Panic Proxy (regime tab PANIC).
 *
 * GET-only (radon-panic-index.timer at 02:50 and 13:15 UTC). Reads through
 * dbFirstRead — Turso scan_snapshots (service='panic-index') first, disk
 * data/panic_index.json fallback — and ALWAYS returns 200; absent data is
 * the contract's `missing: true` shape, never a 4xx.
 *
 * Spec: docs/indicators/panic-index.md.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";
import { getFreshnessWindowMs } from "@/lib/serviceHealthWindows";
import { MISSING_PANIC_INDEX } from "@/lib/panicIndex";

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

function daysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

const DATA_DATE = daysAgo(1);
const DISK_DATA_DATE = daysAgo(2);

function seriesPoint(dateStr: string, level: number, delta: number | null): Payload {
  return {
    date: dateStr,
    vix: 15.44,
    vix3m: 18.55,
    vvix: 87.72,
    ts: 0.8323,
    skew: 145.7,
    z_vix: -0.8252,
    z_vvix: -1.0661,
    z_ts: -0.7683,
    z_skew: 0.1573,
    level,
    delta_1d: delta,
  };
}

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    source_last_modified: {
      vix: "Fri, 18 Sep 2026 01:51:00 GMT",
      vix3m: "Fri, 18 Sep 2026 01:51:00 GMT",
      vvix: "Fri, 18 Sep 2026 12:01:00 GMT",
      skew: "Fri, 18 Sep 2026 21:01:00 GMT",
    },
    data_date: DATA_DATE,
    count: 2,
    delta_count: 1,
    current: {
      date: DATA_DATE,
      level: -0.6256,
      delta_1d: -0.6269,
      delta_z: -1.75,
      delta_std_10y: 0.3574,
      rank_decline_10y: 87,
      rank_surge_10y: 2423,
      rank_n: 2509,
      legs: {
        vix: { value: 15.44, z: -0.8252 },
        vvix: { value: 87.72, z: -1.0661 },
        ts: { value: 0.8323, z: -0.7683, vix3m: 18.55 },
        skew: { value: 145.7, z: 0.1573 },
        skew25d: null,
      },
    },
    stats: {
      high: 3.7143,
      high_date: "2018-02-05",
      low: -2.3411,
      low_date: "2024-08-06",
      avg: -0.0007,
      stddev: 0.3574,
    },
    alert: { last_fired_date: null, last_fired_kind: null },
    series: [seriesPoint(DISK_DATA_DATE, 0.0013, null), seriesPoint(DATA_DATE, -0.6256, -0.6269)],
    ...overrides,
  };
}

function diskPayload(scanTime?: string): Payload {
  return buildPayload({
    ...(scanTime ? { scan_time: scanTime } : {}),
    data_date: DISK_DATA_DATE,
    count: 1,
    current: {
      date: DISK_DATA_DATE,
      level: 0.0013,
      delta_1d: 0.1,
      delta_z: 0.3,
      delta_std_10y: 0.3574,
      rank_decline_10y: 2000,
      rank_surge_10y: 10,
      rank_n: 2509,
      legs: {
        vix: { value: 17.71, z: -0.134 },
        vvix: { value: 95.41, z: -0.392 },
        ts: { value: 0.8976, z: 0.33, vix3m: 19.73 },
        skew: { value: 145.95, z: 0.201 },
        skew25d: null,
      },
    },
    series: [seriesPoint(DISK_DATA_DATE, 0.0013, 0.1)],
  });
}

async function insertSnapshot(payload: Payload, service = "panic-index"): Promise<void> {
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

describe("GET /api/panic-index", () => {
  it("serves the latest Turso snapshot over an older disk cache", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload(staleTime)));
    const { GET } = await import("../app/api/panic-index/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.data_date).toBe(DATA_DATE);
    expect((json.current as Payload).level).toBe(-0.6256);
    expect((json.current as Payload).delta_1d).toBe(-0.6269);
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload()));
    const { GET } = await import("../app/api/panic-index/route");
    const json = await jsonOf(await GET());
    expect(json.data_date).toBe(DISK_DATA_DATE);
    expect((json.series as Payload[]).length).toBe(1);
    expect((json.current as Payload).level).toBe(0.0013);
    expect(json.missing).toBeUndefined();
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/panic-index/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({ ...MISSING_PANIC_INDEX });
  });

  it("only reads the panic-index service's snapshots (no cross-service leak)", async () => {
    await insertSnapshot(buildPayload(), "vixts");
    const { GET } = await import("../app/api/panic-index/route");
    expect((await jsonOf(await GET())).missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/panic-index/route");
    expect(route.dynamic).toBe("force-dynamic");
  });

  it("takes maxAgeMs from getFreshnessWindowMs(panic-index, closed)", () => {
    expect(getFreshnessWindowMs("panic-index", "closed")).toBe(26 * 60 * 60_000);
  });
});

describe("GET /api/panic-index freshness budget", () => {
  it("collapses a 30-hour-old snapshot to the stale shape (26h catalog window)", async () => {
    const ancient = new Date(Date.now() - 30 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: ancient }));
    const { GET } = await import("../app/api/panic-index/route");
    const json = await jsonOf(await GET());
    expect(json.stale).toBe(true);
    expect(json.missing).toBe(true);
    expect(json.current).toBeNull();
  });

  it("still serves a snapshot inside the 26h budget verbatim", async () => {
    const recent = new Date(Date.now() - 12 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: recent }));
    const { GET } = await import("../app/api/panic-index/route");
    const json = await jsonOf(await GET());
    expect(json.stale).toBeUndefined();
    expect(json.missing).toBeUndefined();
    expect((json.current as Payload).delta_1d).toBe(-0.6269);
  });

  it("does not let a newer missing:true payload outrank a complete older one", async () => {
    const older = new Date(Date.now() - 6 * 60 * 60_000).toISOString();
    const newer = new Date(Date.now() - 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: older }));
    mockReadFile.mockResolvedValue(
      JSON.stringify({ missing: true, scan_time: newer, series: [], current: null }),
    );
    const { GET } = await import("../app/api/panic-index/route");
    const json = await jsonOf(await GET());
    expect(json.missing).toBeUndefined();
    expect((json.current as Payload).level).toBe(-0.6256);
  });
});
