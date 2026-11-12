/**
 * @vitest-environment node
 *
 * /api/vixts — VIX / VIX3M term-structure ratio (regime tab VIX TS).
 *
 * GET-only (radon-vixts.timer runs the daily Cboe pull at 02:45 UTC). Reads
 * through dbFirstRead — Turso scan_snapshots (service='vixts') first, last-in-memory fallback — and ALWAYS returns 200; absent data is the
 * contract's `missing: true` shape, never a 4xx.
 *
 * Spec: docs/indicators/vixts.md.
 */
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

// Window-relative dates: sessions derived from today, never hardcoded.
function daysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

const DATA_DATE = daysAgo(1);
const DISK_DATA_DATE = daysAgo(2);

function seriesPoint(dateStr: string, ratio: number): Payload {
  return { date: dateStr, vix: 15.21, vix3m: 17.99, ratio, spx: 7654.32 };
}

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    source_last_modified: {
      vix: "Thu, 27 Aug 2026 01:50:46 GMT",
      vix3m: "Wed, 26 Aug 2026 22:00:57 GMT",
      spx: "Thu, 27 Aug 2026 00:31:07 GMT",
    },
    data_date: DATA_DATE,
    count: 2,
    current: {
      date: DATA_DATE,
      vix: 15.21,
      vix3m: 17.99,
      ratio: 0.8455,
      regime: "CONTANGO",
      spx: 7654.32,
    },
    stats: {
      min: 0.7104,
      max: 1.3437,
      mean: 0.894398,
      median: 0.8846,
      days_backwardation: 325,
      pct_backwardation: 7.6435,
      last_backwardation_date: "2026-04-07",
    },
    series: [seriesPoint(DISK_DATA_DATE, 0.8484), seriesPoint(DATA_DATE, 0.8455)],
    ...overrides,
  };
}

function olderPayload(scanTime?: string): Payload {
  return buildPayload({
    ...(scanTime ? { scan_time: scanTime } : {}),
    data_date: DISK_DATA_DATE,
    count: 1,
    current: {
      date: DISK_DATA_DATE,
      vix: 15.45,
      vix3m: 18.21,
      ratio: 0.8484,
      regime: "CONTANGO",
      spx: 7677.28,
    },
    series: [seriesPoint(DISK_DATA_DATE, 0.8484)],
  });
}

async function insertSnapshot(payload: Payload, service = "vixts"): Promise<void> {
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

describe("GET /api/vixts", () => {
  it("serves the latest Turso snapshot over an older row", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    await insertSnapshot(olderPayload(staleTime));
    const { GET } = await import("../app/api/vixts/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.data_date).toBe(DATA_DATE);
    expect((json.current as Payload).ratio).toBe(0.8455);
    expect((json.current as Payload).regime).toBe("CONTANGO");
    expect((json.current as Payload).vix).toBe(15.21);
    expect((json.current as Payload).vix3m).toBe(17.99);
    expect(json.missing).toBeUndefined();
  });

  it("serves the last good read from memory when Turso has no rows", async () => {
    await insertSnapshot(olderPayload());
    const { GET } = await import("../app/api/vixts/route");
    await GET();
    await db.execute("DELETE FROM scan_snapshots");
    const json = await jsonOf(await GET());
    expect(json.data_date).toBe(DISK_DATA_DATE);
    expect((json.series as Payload[]).length).toBe(1);
    expect((json.current as Payload).ratio).toBe(0.8484);
    expect(json.missing).toBeUndefined();
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/vixts/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      missing: true,
      scan_time: null,
      source_last_modified: null,
      data_date: null,
      current: null,
      stats: null,
      series: [],
    });
  });

  it("only reads the vixts service's snapshots (no cross-service leak)", async () => {
    await insertSnapshot(buildPayload(), "vixcor");
    const { GET } = await import("../app/api/vixts/route");
    expect((await jsonOf(await GET())).missing).toBe(true);
  });

  it("carries the per-file source stamps through to the client", async () => {
    await insertSnapshot(buildPayload());
    const { GET } = await import("../app/api/vixts/route");
    const json = await jsonOf(await GET());
    expect(json.source_last_modified).toEqual({
      vix: "Thu, 27 Aug 2026 01:50:46 GMT",
      vix3m: "Wed, 26 Aug 2026 22:00:57 GMT",
      spx: "Thu, 27 Aug 2026 00:31:07 GMT",
    });
  });

  it("declares force-dynamic per the snapshot route cache contract", async () => {
    const route = await import("../app/api/vixts/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});

// ── R-332 / R-366 / REL-118: the route obeys its own freshness budget ──────
//
// The handler served on `result.ok` ALONE, so the `VIXTS_MAX_AGE_MS` budget
// declared at the top of the route and passed into `dbFirstRead` was computed
// and discarded. Every sibling Cboe/daily route uses
// `result.ok && result.fresh ? result.data : staleCollapse(...)`; the vixcor
// route is the direct twin. A dead `radon-vixts.service` therefore kept
// serving a week-old snapshot with no `stale` or `missing` marker, and
// `VixTsPanel` rendered a confident coloured regime badge for a dead feed.
//
// R-366: the `dbFirstRead` call also omitted `isDegraded: isMissingPayload`,
// so source selection was on timestamp alone and a heartbeat payload carrying
// `missing: true` with a newer scan_time outranked a complete older snapshot.

describe("GET /api/vixts freshness budget", () => {
  it("collapses an eight-day-old snapshot to the stale shape", async () => {
    const ancient = new Date(Date.now() - 8 * 24 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: ancient }));
    const { GET } = await import("../app/api/vixts/route");
    const json = await jsonOf(await GET());

    expect(json.stale).toBe(true);
    expect(json.missing).toBe(true);
    expect(json.scan_time).toBe(ancient);
    expect(json.current).toBeNull();
  });

  it("still serves a snapshot inside the 48h budget verbatim", async () => {
    const recent = new Date(Date.now() - 12 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: recent }));
    const { GET } = await import("../app/api/vixts/route");
    const json = await jsonOf(await GET());

    expect(json.stale).toBeUndefined();
    expect(json.missing).toBeUndefined();
    expect((json.current as Payload).ratio).toBe(0.8455);
  });

  it("does not let a newer missing:true payload outrank a complete older one", async () => {
    const older = new Date(Date.now() - 6 * 60 * 60_000).toISOString();
    const newer = new Date(Date.now() - 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: older }));
    const { GET } = await import("../app/api/vixts/route");
    await GET();
    await insertSnapshot({ missing: true, scan_time: newer, series: [], current: null });
    const json = await jsonOf(await GET());

    expect(json.missing).toBeUndefined();
    expect((json.current as Payload).ratio).toBe(0.8455);
  });
});

describe("GET /api/vixts provisional current session", () => {
  const TODAY = daysAgo(0);
  const liveRow = { date: TODAY, vix: 17.49, vix3m: 19.24, ratio: 0.909, spx: 6700.5 };
  const live = (overrides: Payload = {}): Payload => ({
    scan_time: new Date().toISOString(), row: liveRow, current: { ...liveRow, regime: "CONTANGO" },
    source: "cboe_delayed", quote_time: `${TODAY}T15:44:32`, ...overrides,
  });

  it("appends the vixts:live row as provisional current", async () => {
    const official = buildPayload();
    await insertSnapshot(official);
    await insertSnapshot(live(), "vixts:live");
    const { GET } = await import("../app/api/vixts/route");
    const json = await jsonOf(await GET());
    expect(json.data_date).toBe(TODAY);
    expect(json.current).toEqual({ ...liveRow, regime: "CONTANGO" });
    expect((json.series as Payload[]).at(-1)).toEqual(liveRow);
    expect(json.count).toBe((official.series as Payload[]).length + 1);
    expect(json.provisional).toEqual({ source: "cboe_delayed", quote_time: `${TODAY}T15:44:32`, scan_time: expect.any(String) });
  });

  it("ignores a live row that is not newer than the official close", async () => {
    const official = buildPayload();
    await insertSnapshot(official);
    await insertSnapshot(live({ row: { ...liveRow, date: DATA_DATE } }), "vixts:live");
    const { GET } = await import("../app/api/vixts/route");
    expect(await jsonOf(await GET())).toEqual(official);
  });

  it("ignores a malformed live row", async () => {
    const official = buildPayload();
    await insertSnapshot(official);
    await insertSnapshot(live({ row: { ...liveRow, ratio: "x" } }), "vixts:live");
    const { GET } = await import("../app/api/vixts/route");
    expect(await jsonOf(await GET())).toEqual(official);
  });
});
