/**
 * @vitest-environment node
 *
 * /api/iv-spread — NDX vs SPX 1M ATM implied volatility spread route
 * (regime tab).
 *
 * GET-only: the series updates once per session (radon-iv-spread.timer runs
 * fetch_iv_spread.py directly), so there is no manual-scan POST. Reads
 * through the dbFirstRead chokepoint — Turso scan_snapshots row
 * (service='iv-spread') first, disk data/iv_spread.json fallback — and
 * ALWAYS returns 200; absent, stale and degraded data are all 200, never a
 * 4xx/5xx (feedback_http_status_for_real_errors).
 *
 * The route is a pass-through: it never transforms the payload and never
 * recomputes a spread. The job status "stale_source" reaches the client
 * verbatim; only the absent / beyond-max-age cases collapse to the
 * MISSING_IV_SPREAD shape.
 *
 * Mechanics mirror ivrank-api.test.ts. Spec: docs/indicators/iv-spread.md
 * sections F.2, F.3.
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
 * 2026-09-02 session (spx 0.12104312, ndx 0.1758578, spread 5.481468, z
 * 0.104002 against a 5.318448 mean / 1.567474 stdev) so a drifted contract
 * reads as a wrong number.
 */
function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    status: "ok",
    source: "ib",
    as_of: "2026-09-02",
    expected_session: "2026-09-02",
    market_status: "closed",
    count: 1253,
    spread_count: 1253,
    dropped_unpaired: 0,
    current: {
      date: "2026-09-02",
      spx_iv: 0.12104312,
      ndx_iv: 0.1758578,
      spread: 5.481468,
      z_score: 0.104002,
      pctile: 59.377494,
      change_1d: 0.360352,
      regime: "NORMAL",
    },
    stats: {
      count: 1253,
      high: 12.642458,
      high_date: "2026-06-23",
      low: -3.297135,
      low_date: "2025-04-08",
      mean: 5.318448,
      stdev: 1.567474,
      last: 5.481468,
    },
    excluded: [],
    series: [
      { date: "2021-09-07", spx_iv: 0.12250358, ndx_iv: 0.15679251, spread: 3.428893 },
      { date: "2026-09-02", spx_iv: 0.12104312, ndx_iv: 0.1758578, spread: 5.481468 },
    ],
    ...overrides,
  };
}

const MISSING_IV_SPREAD = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
  excluded: [],
};

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: "INSERT INTO scan_snapshots (service, scan_time, payload) VALUES (?, ?, ?)",
    args: ["iv-spread", scanTime ?? (payload.scan_time as string), JSON.stringify(payload)],
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

describe("GET /api/iv-spread — source selection", () => {
  it("serves the latest Turso snapshot (Turso-first over an older disk cache)", async () => {
    const fresh = buildPayload();
    await insertSnapshot(fresh);
    const stale = buildPayload({
      scan_time: new Date(Date.now() - 6 * 3_600_000).toISOString(),
      status: "stale_source",
    });
    mockReadFile.mockResolvedValue(JSON.stringify(stale));

    const { GET } = await import("../app/api/iv-spread/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.status).toBe("ok");
    expect(body.scan_time).toBe(fresh.scan_time);
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload()));

    const { GET } = await import("../app/api/iv-spread/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.current.spread).toBeCloseTo(5.481468, 6);
    expect(body.missing).toBeUndefined();
  });

  it("serves disk with HTTP 200 when the Turso read throws (never a 5xx)", async () => {
    mockGetDb.mockImplementationOnce(() => {
      throw new Error("hrana stream not found");
    });
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload()));

    const { GET } = await import("../app/api/iv-spread/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.missing).toBeUndefined();
  });

  it("only reads the iv-spread service's snapshots", async () => {
    await db.execute({
      sql: "INSERT INTO scan_snapshots (service, scan_time, payload) VALUES (?, ?, ?)",
      args: ["ivrank", new Date().toISOString(), JSON.stringify({ intruder: true })],
    });

    const { GET } = await import("../app/api/iv-spread/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(MISSING_IV_SPREAD);
  });
});

describe("GET /api/iv-spread — absent and stale data are 200, never 4xx", () => {
  it("returns the contract's missing shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/iv-spread/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(MISSING_IV_SPREAD);
  });

  it("collapses a snapshot older than the 48h budget to the missing shape at 200", async () => {
    const old = new Date(Date.now() - 49 * 3_600_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: old }), old);

    const { GET } = await import("../app/api/iv-spread/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ ...MISSING_IV_SPREAD, stale: true, scan_time: old });
  });

  it("still serves a snapshot inside the 48h budget", async () => {
    const recent = new Date(Date.now() - 47 * 3_600_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: recent }), recent);

    const { GET } = await import("../app/api/iv-spread/route");
    const body = await (await GET()).json();
    expect(body.missing).toBeUndefined();
    expect(body.scan_time).toBe(recent);
  });
});

describe("GET /api/iv-spread — degradation passes through untransformed", () => {
  it("passes a stale_source payload through verbatim", async () => {
    await insertSnapshot(buildPayload({ status: "stale_source" }));

    const { GET } = await import("../app/api/iv-spread/route");
    const body = await (await GET()).json();
    expect(body.status).toBe("stale_source");
    expect(body.current.spread).toBeCloseTo(5.481468, 6);
  });

  it("keeps a null z_score / regime and an excluded session intact", async () => {
    await insertSnapshot(
      buildPayload({
        current: {
          date: "2026-09-02",
          spx_iv: 0.12104312,
          ndx_iv: 0.1758578,
          spread: 5.481468,
          z_score: null,
          pctile: null,
          change_1d: null,
          regime: null,
        },
        excluded: [{ date: "2026-08-17", leg: "spx_iv", iv: 0.2443, prev_iv: 0.1153, next_iv: 0.1251 }],
      }),
    );

    const { GET } = await import("../app/api/iv-spread/route");
    const body = await (await GET()).json();
    expect(body.current.z_score).toBeNull();
    expect(body.current.regime).toBeNull();
    expect(body.excluded).toHaveLength(1);
    expect(body.series[0].spread).toBeCloseTo(3.428893, 6);
  });
});

describe("GET /api/iv-spread — route contract", () => {
  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/iv-spread/route");
    expect(route.dynamic).toBe("force-dynamic");
  });

  it("runs on the node runtime (fs + libsql)", async () => {
    const route = await import("../app/api/iv-spread/route");
    expect(route.runtime).toBe("nodejs");
  });

  it("exposes no POST handler (the systemd timer owns refreshes)", async () => {
    const route = await import("../app/api/iv-spread/route");
    expect("POST" in route).toBe(false);
  });

  it("pins the read capability for the assistant catalog", async () => {
    const route = await import("../app/api/iv-spread/route");
    expect(route.radonCapability).toBe("read");
  });

  it("sets the shared cache headers and the iv-spread cache tag", async () => {
    await insertSnapshot(buildPayload());

    const { GET } = await import("../app/api/iv-spread/route");
    const res = await GET();
    expect(res.headers.get("Cache-Control")).toBe(
      "public, max-age=300, stale-while-revalidate=3600",
    );
    expect(res.headers.get("X-Cache-Tags")).toBe("iv-spread");
  });
});
