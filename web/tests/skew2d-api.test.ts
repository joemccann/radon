/**
 * @vitest-environment node
 *
 * /api/skew2d — two-session change in SPX 1M put/call skew (regime tab).
 *
 * GET-only: radon-skew2d.timer runs fetch_skew2d.py daily at 21:50 UTC.
 * Reads through dbFirstRead (Turso scan_snapshots service='skew2d', disk
 * data/skew2d.json). Always HTTP 200; absent data is missing:true.
 * Spec: docs/indicators/skew2d.md.
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
    source: "skew_history",
    count: 733,
    current: {
      date: "2026-08-07",
      ratio: 1.23838134631528,
      change: -0.0036750737861990235,
      put_iv: 0.15,
      call_iv: 0.12,
      expiry: "2026-08-21",
      dte: 14,
    },
    stats: {
      high: 0.37540121157999673,
      low: -0.2550619076592615,
      avg: -9.866255423597131e-05,
      stddev: 0.04798679737838151,
    },
    series: [
      { date: "2026-08-05", ratio: 1.242, change: -0.07 },
      { date: "2026-08-06", ratio: 1.276, change: 0.08 },
      { date: "2026-08-07", ratio: 1.238, change: -0.0037 },
    ],
    ...overrides,
  };
}

async function insertSnapshot(
  service: string,
  payload: Payload,
  scanTime?: string,
): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES (?, ?, ?)`,
    args: [service, scanTime ?? (payload.scan_time as string), JSON.stringify(payload)],
  });
}

const ENOENT = Object.assign(new Error("ENOENT: no such file"), { code: "ENOENT" });

async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  return (await res.json()) as Record<string, unknown>;
}

beforeEach(async () => {
  db = createClient({ url: ":memory:" });
  await seedSchema(db);
  mockGetDb.mockReturnValue(db);
  mockReadFile.mockReset();
  mockReadFile.mockRejectedValue(ENOENT);
});

afterEach(() => {
  db.close();
});

describe("/api/skew2d", () => {
  it("route is force-dynamic + nodejs runtime", async () => {
    const route = await import("@/app/api/skew2d/route");
    expect(route.dynamic).toBe("force-dynamic");
    expect(route.runtime).toBe("nodejs");
  });

  it("returns Turso snapshot when present", async () => {
    const payload = buildPayload({ scan_time: new Date(Date.now() - 60_000).toISOString() });
    await insertSnapshot("skew2d", payload);
    const { GET } = await import("@/app/api/skew2d/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await jsonOf(res);
    expect(body.missing).toBeUndefined();
    expect(body.count).toBe(733);
    expect(body.source).toBe("skew_history");
    expect((body.current as { change: number }).change).toBeCloseTo(
      -0.0036750737861990235,
      12,
    );
  });

  it("falls back to disk when Turso has no row", async () => {
    const payload = buildPayload({ scan_time: new Date(Date.now() - 120_000).toISOString(), count: 700 });
    mockReadFile.mockResolvedValue(JSON.stringify(payload));
    const { GET } = await import("@/app/api/skew2d/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await jsonOf(res);
    expect(body.count).toBe(700);
  });

  it("prefers fresher Turso over older disk", async () => {
    const older = buildPayload({ scan_time: new Date(Date.now() - 120_000).toISOString(), count: 700 });
    const newer = buildPayload({ scan_time: new Date(Date.now() - 60_000).toISOString(), count: 733 });
    mockReadFile.mockResolvedValue(JSON.stringify(older));
    await insertSnapshot("skew2d", newer);
    const { GET } = await import("@/app/api/skew2d/route");
    const body = await jsonOf(await GET());
    expect(body.count).toBe(733);
  });

  it("missing contract is HTTP 200 with frozen shape", async () => {
    const { GET } = await import("@/app/api/skew2d/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      missing: true,
      scan_time: null,
      count: 0,
      series: [],
      current: null,
      stats: null,
    });
  });

  it("does not leak a sibling service snapshot", async () => {
    await insertSnapshot("skew", buildPayload({ count: 999 }));
    const { GET } = await import("@/app/api/skew2d/route");
    const body = await jsonOf(await GET());
    expect(body).toMatchObject({ missing: true, count: 0 });
  });

  it("expired snapshots are not returned as current", async () => {
    const old = buildPayload({ scan_time: "2026-01-01T21:50:00.000Z" });
    await insertSnapshot("skew2d", old);
    const { GET } = await import("@/app/api/skew2d/route");
    expect(await jsonOf(await GET())).toMatchObject({ missing: true, count: 0 });
  });
});
