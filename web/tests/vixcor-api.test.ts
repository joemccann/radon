/**
 * @vitest-environment node
 *
 * /api/vixcor — VIX vs Cboe COR3M 20-session correlation route (regime tab).
 *
 * GET-only: the series updates once per session (radon-vixcor.timer runs
 * fetch_vixcor.py directly), so there is no manual-scan POST. Reads through
 * the dbFirstRead chokepoint — Turso scan_snapshots row (service='vixcor')
 * first, disk data/vixcor.json fallback — and ALWAYS returns 200; absent,
 * stale, degraded and parent-lagging data are all 200, never a 4xx/5xx
 * (feedback_http_status_for_real_errors).
 *
 * The route is a pass-through: it never transforms the payload and never
 * recomputes a statistic. The three job statuses ("ok" | "holding" |
 * "stale_parent") therefore reach the client verbatim, and only the absent /
 * beyond-max-age cases collapse to the MISSING_VIXCOR shape.
 *
 * Mechanics mirror cor-api.test.ts: @/lib/db backed by a real in-memory
 * libsql client seeded with the scan_snapshots schema; fs mocked.
 * Spec: docs/indicators/vixcor.md sections F.2, F.3, F.4.
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
 * The F.3 payload shape. Numbers are the spec's calibration values for the
 * 2026-08-14 session (VIX close 14.25, corr20 +0.014969, the open fifth
 * breakdown episode) so a drifted contract reads as a wrong number, not a
 * vague shape mismatch.
 */
function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    source_last_modified: { vix: "Sat, 15 Aug 2026 23:01:11 GMT" },
    status: "ok",
    as_of: "2026-08-14",
    parent_as_of: "2026-08-14",
    vix_as_of: "2026-08-14",
    expected_session: "2026-08-14",
    lag_sessions: 0,
    market_status: "closed",
    window: 20,
    count: 5171,
    corr_count: 5152,
    current: {
      date: "2026-08-14",
      vix_close: 14.25,
      cor3m_close: 12.34,
      corr20: 0.014969,
      change_1d: -0.0413,
      percentile: 0.0181,
      regime: "DECOUPLED",
      vix_cov_20d: 0.0512,
      episode: {
        trigger: "2026-08-11",
        start: "2026-08-11",
        end: "2026-08-14",
        sessions: 4,
        trough: 0.014969,
        trough_date: "2026-08-14",
        vix_at_trigger: 15.28,
        open: true,
      },
    },
    stats: {
      min: -0.5324,
      p01: -0.1135,
      p05: 0.2586,
      p10: 0.457,
      p25: 0.6795,
      median: 0.8446,
      p75: 0.925,
      p90: 0.9582,
      p95: 0.9723,
      p99: 0.9857,
      max: 0.9932,
      mean: 0.7621,
      stddev: 0.2341,
      share_below_zero: 0.0177,
      share_below_trigger: 0.0489,
      vix_cov_breakdown: 0.0691,
      vix_cov_coupled: 0.118,
    },
    episodes: [
      {
        trigger: "2026-05-22",
        start: "2026-05-21",
        end: "2026-05-26",
        sessions: 4,
        trough: 0.235,
        trough_date: "2026-05-22",
        corr_at_trigger: 0.235,
        vix_at_trigger: 16.7,
        open: false,
        forward: { "5": 0.058, "10": 0.33, "21": 0.554, "42": 0.622, "63": null },
      },
      {
        trigger: "2026-08-11",
        start: "2026-08-11",
        end: "2026-08-14",
        sessions: 4,
        trough: 0.014969,
        trough_date: "2026-08-14",
        corr_at_trigger: 0.233,
        vix_at_trigger: 15.28,
        open: true,
        forward: { "5": null, "10": null, "21": null, "42": null, "63": null },
      },
    ],
    forward_stats: {
      horizons: [5, 10, 21, 42, 63],
      event: {
        "5": { n: 30, mean_drawup: 0.0392, median_drawup: 0.0301, p_higher: 0.4, p_drawup_20: 0.067 },
      },
      base: {
        "5": { n: 5147, mean_drawup: 0.0896, median_drawup: 0.0546, p_higher: 0.462, p_drawup_20: 0.154 },
      },
    },
    series: [
      { date: "2026-08-13", vix_close: 14.86, cor3m_close: 12.1, corr20: 0.0563, episode: true },
      { date: "2026-08-14", vix_close: 14.25, cor3m_close: 12.34, corr20: 0.014969, episode: true },
    ],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('vixcor', ?, ?)`,
    args: [scanTime ?? (payload.scan_time as string), JSON.stringify(payload)],
  });
}

const ENOENT = Object.assign(new Error("ENOENT: no such file"), { code: "ENOENT" });

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  return (await res.json()) as Record<string, unknown>;
}

/** The exact F.2 absent-data contract. Equality-pinned: no extra keys, no missing keys. */
const MISSING_VIXCOR = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  count: 0,
  series: [],
  episodes: [],
  current: null,
  stats: null,
  forward_stats: null,
};

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

describe("GET /api/vixcor — source selection", () => {
  it("serves the latest Turso snapshot (Turso-first over an older disk cache)", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(
      JSON.stringify(buildPayload({ scan_time: staleTime, count: 1 })),
    );

    const { GET } = await import("../app/api/vixcor/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(5171);
    expect(json.corr_count).toBe(5152);
    expect((json.current as Payload).corr20).toBe(0.014969);
    expect((json.current as Payload).vix_close).toBe(14.25);
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload({ count: 5170 })));

    const { GET } = await import("../app/api/vixcor/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(5170);
    expect(json.missing).toBeUndefined();
  });

  it("serves disk with HTTP 200 when the Turso read throws (never a 5xx)", async () => {
    mockGetDb.mockReturnValue({
      execute: () => Promise.reject(new Error("hrana: stream closed")),
    } as unknown as Client);
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload({ count: 5169 })));

    const { GET } = await import("../app/api/vixcor/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(5169);
  });

  it("only reads the vixcor service's snapshots", async () => {
    await db.execute({
      sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('cor', ?, ?)`,
      args: [new Date().toISOString(), JSON.stringify(buildPayload({ count: 5 }))],
    });

    const { GET } = await import("../app/api/vixcor/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json).toEqual(MISSING_VIXCOR);
  });
});

describe("GET /api/vixcor — absent and stale data are 200, never 4xx", () => {
  it("returns the contract's missing shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/vixcor/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual(MISSING_VIXCOR);
  });

  it("collapses a snapshot older than the 48h budget to the missing shape at 200", async () => {
    // radon-vixcor.timer fires daily at 02:35 UTC including weekends, so a
    // snapshot three days old means the writer is down, not merely idle.
    const threeDaysAgo = new Date(Date.now() - 72 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: threeDaysAgo }), threeDaysAgo);

    const { GET } = await import("../app/api/vixcor/route");
    const res = await GET();
    expect(res.status).toBe(200);
    // R-194: the collapse keeps the stale row's own scan_time and marks it
    // stale, so "the feed died three days ago" is distinguishable from "this
    // job has never run". Everything else is still the missing shape.
    expect(await jsonOf(res)).toEqual({
      ...MISSING_VIXCOR,
      stale: true,
      scan_time: threeDaysAgo,
    });
  });

  it("still serves a snapshot inside the 48h budget", async () => {
    const yesterday = new Date(Date.now() - 26 * 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: yesterday }), yesterday);

    const { GET } = await import("../app/api/vixcor/route");
    const res = await GET();
    const json = await jsonOf(res);
    expect(json.missing).toBeUndefined();
    expect(json.count).toBe(5171);
  });

  it("never answers with a 4xx or 5xx in any degradation state", async () => {
    const { GET } = await import("../app/api/vixcor/route");

    const absent = await GET();
    expect(absent.status).toBe(200);

    await insertSnapshot(buildPayload({ status: "stale_parent", lag_sessions: 9 }));
    const degraded = await GET();
    expect(degraded.status).toBe(200);
    expect(degraded.status).toBeLessThan(300);
  });
});

describe("GET /api/vixcor — degradation statuses pass through untransformed", () => {
  it("passes a holding payload (parent one session behind) through verbatim", async () => {
    const holding = buildPayload({
      status: "holding",
      as_of: "2026-08-13",
      parent_as_of: "2026-08-13",
      vix_as_of: "2026-08-14",
      expected_session: "2026-08-14",
      lag_sessions: 1,
    });
    await insertSnapshot(holding);

    const { GET } = await import("../app/api/vixcor/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.status).toBe("holding");
    expect(json.lag_sessions).toBe(1);
    expect(json.as_of).toBe("2026-08-13");
    expect(json.vix_as_of).toBe("2026-08-14");
    expect(json.missing).toBeUndefined();
    // Pass-through: the route recomputes nothing.
    expect(json).toEqual(holding);
  });

  it("passes a stale_parent payload through verbatim", async () => {
    const stale = buildPayload({
      status: "stale_parent",
      as_of: "2026-08-11",
      parent_as_of: "2026-08-11",
      expected_session: "2026-08-14",
      lag_sessions: 3,
    });
    await insertSnapshot(stale);

    const { GET } = await import("../app/api/vixcor/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.status).toBe("stale_parent");
    expect(json.lag_sessions).toBe(3);
    expect(json).toEqual(stale);
  });

  it("keeps the null-corr leading rows and the episode flags intact", async () => {
    const payload = buildPayload({
      series: [
        { date: "2006-01-03", vix_close: 11.19, cor3m_close: 40.12, corr20: null, episode: false },
        { date: "2026-08-14", vix_close: 14.25, cor3m_close: 12.34, corr20: 0.014969, episode: true },
      ],
    });
    await insertSnapshot(payload);

    const { GET } = await import("../app/api/vixcor/route");
    const json = await jsonOf(await GET());
    const series = json.series as Array<Record<string, unknown>>;
    expect(series[0].corr20).toBeNull();
    expect(series[0].episode).toBe(false);
    expect(series[1].episode).toBe(true);
  });

  it("keeps the open episode marked open and the resolved one resolved", async () => {
    await insertSnapshot(buildPayload());

    const { GET } = await import("../app/api/vixcor/route");
    const json = await jsonOf(await GET());
    const episodes = json.episodes as Array<Record<string, unknown>>;
    expect(episodes).toHaveLength(2);
    expect(episodes[0].open).toBe(false);
    expect(episodes[1].open).toBe(true);
    expect(episodes[1].trigger).toBe("2026-08-11");
  });

  it("carries both the event and the all-session base-rate buckets", async () => {
    await insertSnapshot(buildPayload());

    const { GET } = await import("../app/api/vixcor/route");
    const json = await jsonOf(await GET());
    const forward = json.forward_stats as Record<string, Record<string, Record<string, number>>>;
    expect(forward.horizons).toEqual([5, 10, 21, 42, 63]);
    // The validation study's verdict: post-breakdown drawup sits BELOW the
    // unconditional base rate. The payload must carry the null alongside the
    // event bucket so the UI can never show one without the other.
    expect(forward.event["5"].mean_drawup).toBe(0.0392);
    expect(forward.base["5"].mean_drawup).toBe(0.0896);
    expect(forward.event["5"].mean_drawup).toBeLessThan(forward.base["5"].mean_drawup);
  });
});

describe("GET /api/vixcor — route contract", () => {
  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/vixcor/route");
    expect(route.dynamic).toBe("force-dynamic");
  });

  it("runs on the node runtime (fs + libsql)", async () => {
    const route = await import("../app/api/vixcor/route");
    expect(route.runtime).toBe("nodejs");
  });

  it("exposes no POST handler (the systemd timer owns refreshes)", async () => {
    const route = (await import("../app/api/vixcor/route")) as Record<string, unknown>;
    expect(route.POST).toBeUndefined();
  });

  it("sets the shared cache headers and the vixcor cache tag", async () => {
    await insertSnapshot(buildPayload());

    const { GET } = await import("../app/api/vixcor/route");
    const res = await GET();
    expect(res.headers.get("Cache-Control")).toBe(
      "public, max-age=300, stale-while-revalidate=3600",
    );
    expect(res.headers.get("X-Cache-Tags")).toBe("vixcor");
  });
});
