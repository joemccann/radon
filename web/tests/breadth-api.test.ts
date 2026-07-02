/**
 * @vitest-environment node
 *
 * /api/breadth — NYSE advance/decline breadth snapshot route.
 *
 * GET reads through the dbFirstRead chokepoint (Turso breadth_snapshots
 * first, disk data/breadth.json fallback) and ALWAYS returns 200 — absent
 * data is the contract's `missing: true` shape, never a 4xx. POST forwards
 * to FastAPI POST /breadth/scan via radonFetch, falling back to the cached
 * snapshot (with an X-Sync-Warning header) when the scan fails.
 *
 * Mechanics mirror alerts-api.test.ts: @/lib/db is backed by a real
 * in-memory libsql client seeded with the breadth_snapshots schema so the
 * SQL executes for real; fs reads and radonFetch are mocked.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: mockGetDb,
}));

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({
  radonFetch: (...args: unknown[]) => mockRadonFetch(...args),
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
  await client.execute(`CREATE TABLE breadth_snapshots (
    taken_at TEXT NOT NULL,
    payload TEXT NOT NULL)`);
}

type Payload = Record<string, unknown>;

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    market_open: false,
    source: "ib",
    latest: {
      session_date: "2026-07-01",
      net_ad: 820,
      net_ad_prev: -140,
      tick: 250,
      cum_ad: 51230,
      cum_ad_change_20d: 1900,
      spy_change_20d_pct: 2.4,
      sessions_positive_20: 13,
      divergence: "none",
    },
    history: [
      { date: "2026-06-30", net_ad: -140, cum_ad: 50410, spy_close: 612.1 },
      { date: "2026-07-01", net_ad: 820, cum_ad: 51230, spy_close: 615.4 },
    ],
    intraday: [
      { time: "2026-07-01T13:35:00Z", net_ad: 120 },
      { time: "2026-07-01T13:40:00Z", net_ad: -60 },
    ],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, takenAt?: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO breadth_snapshots (taken_at, payload) VALUES (?, ?)`,
    args: [takenAt ?? (payload.scan_time as string), JSON.stringify(payload)],
  });
}

const ENOENT = Object.assign(new Error("ENOENT: no such file"), { code: "ENOENT" });

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  return (await res.json()) as Record<string, unknown>;
}

beforeEach(async () => {
  vi.resetModules();
  db = createClient({ url: ":memory:" });
  await seedSchema(db);
  mockGetDb.mockReturnValue(db);
  mockReadFile.mockRejectedValue(ENOENT);
  mockRadonFetch.mockReset();
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  db.close();
  vi.restoreAllMocks();
});

describe("GET /api/breadth", () => {
  it("serves the latest Turso snapshot (Turso-first over an older disk cache)", async () => {
    const dbPayload = buildPayload();
    await insertSnapshot(dbPayload);
    // Older disk snapshot must NOT win.
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    mockReadFile.mockResolvedValue(
      JSON.stringify(buildPayload({ scan_time: staleTime, source: "cache" })),
    );

    const { GET } = await import("../app/api/breadth/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.source).toBe("ib");
    expect((json.latest as Payload).net_ad).toBe(820);
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    const diskPayload = buildPayload({ source: "cache" });
    ((diskPayload.latest as Payload).net_ad as unknown) = 111;
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload));

    const { GET } = await import("../app/api/breadth/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.source).toBe("cache");
    expect((json.latest as Payload).net_ad).toBe(111);
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/breadth/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json).toEqual({
      missing: true,
      scan_time: null,
      history: [],
      intraday: [],
      latest: null,
    });
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/breadth/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});

describe("POST /api/breadth", () => {
  it("forwards to FastAPI POST /breadth/scan via radonFetch and returns its payload", async () => {
    const scanned = buildPayload();
    mockRadonFetch.mockResolvedValue(scanned);

    const { POST } = await import("../app/api/breadth/route");
    const res = await POST();
    expect(res.status).toBe(200);
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/breadth/scan",
      expect.objectContaining({ method: "POST" }),
    );
    const json = await jsonOf(res);
    expect((json.latest as Payload).net_ad).toBe(820);
  });

  it("falls back to the cached snapshot with an X-Sync-Warning header when the scan fails", async () => {
    await insertSnapshot(buildPayload());
    mockRadonFetch.mockRejectedValue(new Error("gateway awaiting 2FA"));

    const { POST } = await import("../app/api/breadth/route");
    const res = await POST();
    expect(res.status).toBe(200);
    expect(res.headers.get("X-Sync-Warning")).toContain("gateway awaiting 2FA");
    const json = await jsonOf(res);
    expect((json.latest as Payload).net_ad).toBe(820);
  });

  it("returns 502 with the coerced error when the scan fails and no cache exists", async () => {
    mockRadonFetch.mockRejectedValue(new Error("scan exploded"));

    const { POST } = await import("../app/api/breadth/route");
    const res = await POST();
    expect(res.status).toBe(502);
    const json = await jsonOf(res);
    expect(json.error).toContain("scan exploded");
  });
});
