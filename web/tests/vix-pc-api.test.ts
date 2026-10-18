/**
 * @vitest-environment node
 *
 * /api/vix-pc — 5-session cumulative VIX put/call route (regime tab).
 *
 * GET-only: radon-vix-pc.timer runs fetch_vix_pc.py directly. Reads through
 * dbFirstRead. Absent data is HTTP 200 + missing:true, never a 4xx.
 *
 * Spec: docs/indicators/vix-pc.md section F.
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

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    status: "ok",
    source: "uw",
    as_of: "2026-10-16",
    data_date: "2026-10-16",
    expected_session: "2026-10-16",
    market_status: "closed",
    window: 5,
    count: 500,
    cum5_count: 496,
    current: {
      date: "2026-10-16",
      put_volume: 573686,
      call_volume: 1729988,
      daily_ratio: 0.33161270482801036,
      cum5_ratio: 0.3188487844029556,
      percentile: 4.032258064516129,
      regime: "CAUTIOUS",
      series_min: 0.2417297884911582,
      series_max: 1.6973206456466932,
      series_p10: 0.3435408949777816,
      series_p90: 0.6760256318262985,
      series_min_date: "2026-10-08",
      is_series_low: false,
    },
    stats: {
      min: 0.2417297884911582,
      p10: 0.3435408949777816,
      p25: 0.3943868581804748,
      median: 0.48050480250544647,
      p75: 0.5889207941175402,
      p90: 0.6760256318262985,
      max: 1.6973206456466932,
      mean: 0.5057092921999375,
    },
    series: [
      {
        date: "2024-10-18",
        put_volume: 1,
        call_volume: 1,
        daily_ratio: 1,
        cum5_ratio: null,
      },
      {
        date: "2026-10-16",
        put_volume: 573686,
        call_volume: 1729988,
        daily_ratio: 0.33161270482801036,
        cum5_ratio: 0.3188487844029556,
      },
    ],
    ...overrides,
  };
}

const MISSING_VIX_PC = {
  missing: true,
  status: "missing",
  scan_time: null,
  as_of: null,
  data_date: null,
  count: 0,
  series: [],
  current: null,
  stats: null,
};

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: "INSERT INTO scan_snapshots (service, scan_time, payload) VALUES (?, ?, ?)",
    args: ["vix-pc", scanTime ?? (payload.scan_time as string), JSON.stringify(payload)],
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

describe("GET /api/vix-pc — source selection", () => {
  it("serves the latest Turso snapshot (Turso-first over an older disk cache)", async () => {
    const fresh = buildPayload();
    await insertSnapshot(fresh);
    const stale = buildPayload({
      scan_time: new Date(Date.now() - 6 * 3_600_000).toISOString(),
      source: "uw",
    });
    mockReadFile.mockResolvedValue(JSON.stringify(stale));

    const { GET } = await import("../app/api/vix-pc/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.source).toBe("uw");
    expect(body.scan_time).toBe(fresh.scan_time);
    expect(body.current.cum5_ratio).toBeCloseTo(0.3188487844029556, 6);
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    const diskPayload = buildPayload();
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload));

    const { GET } = await import("../app/api/vix-pc/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.current.cum5_ratio).toBeCloseTo(0.3188487844029556, 6);
    expect(body.missing).toBeUndefined();
  });

  it("serves disk with HTTP 200 when the Turso read throws (never a 5xx)", async () => {
    mockGetDb.mockImplementationOnce(() => {
      throw new Error("hrana stream not found");
    });
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload()));

    const { GET } = await import("../app/api/vix-pc/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.missing).toBeUndefined();
  });

  it("only reads the vix-pc service's snapshots", async () => {
    await db.execute({
      sql: "INSERT INTO scan_snapshots (service, scan_time, payload) VALUES (?, ?, ?)",
      args: ["vixcor", new Date().toISOString(), JSON.stringify({ intruder: true })],
    });

    const { GET } = await import("../app/api/vix-pc/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual(MISSING_VIX_PC);
  });
});

describe("GET /api/vix-pc — absent and stale data are 200, never 4xx", () => {
  it("returns the contract's missing shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/vix-pc/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(MISSING_VIX_PC);
  });

  it("collapses a snapshot older than the 48h budget to the missing shape at 200", async () => {
    const old = new Date(Date.now() - 49 * 3_600_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: old }), old);

    const { GET } = await import("../app/api/vix-pc/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ ...MISSING_VIX_PC, stale: true, scan_time: old });
  });

  it("still serves a snapshot inside the 48h budget", async () => {
    const recent = new Date(Date.now() - 47 * 3_600_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: recent }), recent);

    const { GET } = await import("../app/api/vix-pc/route");
    const res = await GET();
    const body = await res.json();
    expect(body.missing).toBeUndefined();
    expect(body.scan_time).toBe(recent);
  });
});

describe("GET /api/vix-pc — degradation statuses pass through untransformed", () => {
  it("passes a stale_source payload through verbatim", async () => {
    await insertSnapshot(buildPayload({ status: "stale_source" }));

    const { GET } = await import("../app/api/vix-pc/route");
    const body = await (await GET()).json();
    expect(body.status).toBe("stale_source");
  });

  it("keeps null-cum5 leading rows intact", async () => {
    await insertSnapshot(buildPayload());

    const { GET } = await import("../app/api/vix-pc/route");
    const body = await (await GET()).json();
    expect(body.series[0].cum5_ratio).toBeNull();
    expect(body.series[1].cum5_ratio).toBeCloseTo(0.3188487844029556, 6);
  });
});

describe("GET /api/vix-pc — route contract", () => {
  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/vix-pc/route");
    expect(route.dynamic).toBe("force-dynamic");
  });

  it("runs on the node runtime (fs + libsql)", async () => {
    const route = await import("../app/api/vix-pc/route");
    expect(route.runtime).toBe("nodejs");
  });

  it("exposes no POST handler (the systemd timer owns refreshes)", async () => {
    const route = await import("../app/api/vix-pc/route");
    expect("POST" in route).toBe(false);
  });

  it("sets the shared cache headers and the vix-pc cache tag", async () => {
    await insertSnapshot(buildPayload());

    const { GET } = await import("../app/api/vix-pc/route");
    const res = await GET();
    expect(res.headers.get("Cache-Control")).toBe(
      "private, max-age=300, stale-while-revalidate=3600",
    );
    expect(res.headers.get("X-Cache-Tags")).toBe("vix-pc");
  });

  it("exports radonCapability read", async () => {
    const route = await import("../app/api/vix-pc/route");
    expect(route.radonCapability).toBe("read");
  });
});
