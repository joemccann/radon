/**
 * @vitest-environment node
 *
 * /api/ivrank — SPY 1M implied volatility rank route (regime tab).
 *
 * GET-only: the series updates once per session (radon-ivrank.timer runs
 * fetch_ivrank.py directly), so there is no manual-scan POST. Reads through
 * the dbFirstRead chokepoint — Turso scan_snapshots row (service='ivrank')
 * first, disk data/ivrank.json fallback — and ALWAYS returns 200; absent,
 * stale and degraded data are all 200, never a 4xx/5xx
 * (feedback_http_status_for_real_errors).
 *
 * The route is a pass-through: it never transforms the payload and never
 * recomputes a rank. The job statuses ("ok" | "degraded_uw" |
 * "stale_source") reach the client verbatim; only the absent /
 * beyond-max-age cases collapse to the MISSING_IVRANK shape.
 *
 * Mechanics mirror vixcor-api.test.ts: @/lib/db backed by a real in-memory
 * libsql client seeded with the scan_snapshots schema; fs mocked.
 * Spec: docs/indicators/ivrank.md sections F.2, F.3.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: mockGetDb,
}));

const mockReadFile = vi.fn();
vi.mock("fs/promises", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs/promises")>();
  return {
    ...actual,
    readFile: (...args: unknown[]) => mockReadFile(...args),
  };
});

async function seedSchema(client: Client): Promise<void> {
  await client.execute(`CREATE TABLE scan_snapshots (
    service TEXT NOT NULL,
    scan_time TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (service, scan_time))`);
}

type Payload = Record<string, unknown>;

/**
 * The F.3 payload shape. Numbers are the fixture calibration values for the
 * 2026-08-21 session (iv 0.12201147, rank 10.559822 inside a 0.10542261 ..
 * 0.26251674 1y range) so a drifted contract reads as a wrong number.
 */
function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    status: "ok",
    source: "ib",
    as_of: "2026-08-21",
    expected_session: "2026-08-21",
    market_status: "closed",
    rank_window: 252,
    count: 1260,
    rank_count: 1009,
    current: {
      date: "2026-08-21",
      iv: 0.12201147,
      iv_rank: 10.559822,
      iv_pct: 11.952191,
      iv_1y_low: 0.10542261,
      iv_1y_high: 0.26251674,
      rank_change_1d: -1.2,
      regime: "SUPPRESSED",
    },
    uw_check: { date: "2026-08-21", iv_rank: 10.58 },
    stats: {
      min: 0,
      p25: 18.4,
      median: 41.2,
      p75: 66,
      max: 100,
      mean: 43.1,
      share_suppressed: 0.24,
      share_extreme: 0.06,
    },
    series: [
      { date: "2021-08-23", iv: 0.141, iv_rank: null, iv_pct: null },
      { date: "2026-08-21", iv: 0.12201147, iv_rank: 10.559822, iv_pct: 11.952191 },
    ],
    ...overrides,
  };
}

const MISSING_IVRANK = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
  uw_check: null,
};

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: "INSERT INTO scan_snapshots (service, scan_time, payload) VALUES (?, ?, ?)",
    args: ["ivrank", scanTime ?? (payload.scan_time as string), JSON.stringify(payload)],
  });
}

beforeEach(async () => {
  vi.resetModules();
  db = createClient({ url: ":memory:" });
  await seedSchema(db);
  mockReadFile.mockReset();
  mockReadFile.mockRejectedValue(Object.assign(new Error("ENOENT"), { code: "ENOENT" }));
});

afterEach(() => {
  db.close();
});

describe("GET /api/ivrank — source selection", () => {
  it("serves the latest Turso snapshot (Turso-first over an older disk cache)", async () => {
    const fresh = buildPayload();
    await insertSnapshot(fresh);
    const stale = buildPayload({
      scan_time: new Date(Date.now() - 6 * 3_600_000).toISOString(),
      source: "uw",
    });
    mockReadFile.mockResolvedValue(JSON.stringify(stale));

    const { GET } = await import("../app/api/ivrank/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.source).toBe("ib");
    expect(body.scan_time).toBe(fresh.scan_time);
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    const diskPayload = buildPayload();
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload));

    const { GET } = await import("../app/api/ivrank/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.current.iv_rank).toBeCloseTo(10.559822, 6);
    expect(body.missing).toBeUndefined();
  });

  it("serves disk with HTTP 200 when the Turso read throws (never a 5xx)", async () => {
    mockGetDb.mockImplementationOnce(() => {
      throw new Error("hrana stream not found");
    });
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload()));

    const { GET } = await import("../app/api/ivrank/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.missing).toBeUndefined();
  });

  it("only reads the ivrank service's snapshots", async () => {
    await db.execute({
      sql: "INSERT INTO scan_snapshots (service, scan_time, payload) VALUES (?, ?, ?)",
      args: ["vixcor", new Date().toISOString(), JSON.stringify({ intruder: true })],
    });

    const { GET } = await import("../app/api/ivrank/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual(MISSING_IVRANK);
  });
});

describe("GET /api/ivrank — absent and stale data are 200, never 4xx", () => {
  it("returns the contract's missing shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/ivrank/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(MISSING_IVRANK);
  });

  it("collapses a snapshot older than the 48h budget to the missing shape at 200", async () => {
    const old = new Date(Date.now() - 49 * 3_600_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: old }), old);

    const { GET } = await import("../app/api/ivrank/route");
    const res = await GET();
    expect(res.status).toBe(200);
    // R-194: the collapse keeps the stale row's own scan_time and marks
    // it stale, so "the feed died on the 20th" is distinguishable from
    // "this job has never run". Everything else is still the missing shape.
    const body = await res.json();
    expect(body).toEqual({ ...MISSING_IVRANK, stale: true, scan_time: old });
  });

  it("still serves a snapshot inside the 48h budget", async () => {
    const recent = new Date(Date.now() - 47 * 3_600_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: recent }), recent);

    const { GET } = await import("../app/api/ivrank/route");
    const res = await GET();
    const body = await res.json();
    expect(body.missing).toBeUndefined();
    expect(body.scan_time).toBe(recent);
  });
});

describe("GET /api/ivrank — degradation statuses pass through untransformed", () => {
  it("passes a degraded_uw payload through verbatim", async () => {
    await insertSnapshot(buildPayload({ status: "degraded_uw", source: "uw" }));

    const { GET } = await import("../app/api/ivrank/route");
    const body = await (await GET()).json();
    expect(body.status).toBe("degraded_uw");
    expect(body.source).toBe("uw");
    expect(body.current.iv_rank).toBeCloseTo(10.559822, 6);
  });

  it("passes a stale_source payload through verbatim", async () => {
    await insertSnapshot(buildPayload({ status: "stale_source" }));

    const { GET } = await import("../app/api/ivrank/route");
    const body = await (await GET()).json();
    expect(body.status).toBe("stale_source");
  });

  it("keeps null-rank leading rows and a null uw_check intact", async () => {
    await insertSnapshot(buildPayload({ uw_check: null }));

    const { GET } = await import("../app/api/ivrank/route");
    const body = await (await GET()).json();
    expect(body.uw_check).toBeNull();
    expect(body.series[0].iv_rank).toBeNull();
    expect(body.series[0].iv_pct).toBeNull();
    expect(body.series[1].iv_rank).toBeCloseTo(10.559822, 6);
  });
});

describe("GET /api/ivrank — route contract", () => {
  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/ivrank/route");
    expect(route.dynamic).toBe("force-dynamic");
  });

  it("runs on the node runtime (fs + libsql)", async () => {
    const route = await import("../app/api/ivrank/route");
    expect(route.runtime).toBe("nodejs");
  });

  it("exposes no POST handler (the systemd timer owns refreshes)", async () => {
    const route = await import("../app/api/ivrank/route");
    expect("POST" in route).toBe(false);
  });

  it("sets the shared cache headers and the ivrank cache tag", async () => {
    await insertSnapshot(buildPayload());

    const { GET } = await import("../app/api/ivrank/route");
    const res = await GET();
    expect(res.headers.get("Cache-Control")).toBe(
      "public, max-age=300, stale-while-revalidate=3600",
    );
    expect(res.headers.get("X-Cache-Tags")).toBe("ivrank");
  });
});
