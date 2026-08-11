/**
 * @vitest-environment node
 *
 * /api/equibles-short-crowding — short crowding / squeeze scoreboard read route.
 *
 * GET-only: the refresh job owns the Equibles pull, so there is no manual-scan
 * POST. Reads through the dbFirstRead chokepoint (Turso scan_snapshots row
 * service='equibles-short-crowding' first, data/equibles_short_crowding.json
 * fallback) and ALWAYS returns 200; absent data is the contract's
 * `missing: true` shape, never a 4xx.
 *
 * Split from equibles-short-crowding.test.tsx because the panel needs jsdom and
 * jsdom cannot intercept the `fs/promises` mock the disk fallback depends on.
 *
 * Mechanics mirror margin-debt-api.test.ts.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: () => mockGetDb(),
}));

const mockReadFile = vi.fn();
vi.mock("fs/promises", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs/promises")>();
  return {
    ...actual,
    readFile: (...args: unknown[]) => mockReadFile(...args),
  };
});

type Payload = Record<string, unknown>;

function daysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

const SETTLEMENT = daysAgo(9);

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    count: 2,
    latest_settlement_date: SETTLEMENT,
    requests_used: 5,
    entries: [
      { ticker: "GME", settlement_date: SETTLEMENT, days_to_cover: 6.4, squeeze_score: 88 },
      { ticker: "AAPL", settlement_date: SETTLEMENT, days_to_cover: 1.1, squeeze_score: 24 },
    ],
    board: [],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload)
          VALUES ('equibles-short-crowding', ?, ?)`,
    args: [scanTime ?? (payload.scan_time as string), JSON.stringify(payload)],
  });
}

const ENOENT = Object.assign(new Error("ENOENT: no such file"), { code: "ENOENT" });

async function callRoute(): Promise<Payload> {
  const { GET } = await import("../app/api/equibles-short-crowding/route");
  const res = await GET();
  expect(res.status).toBe(200);
  return (await res.json()) as Payload;
}

beforeEach(async () => {
  vi.resetModules();
  db = createClient({ url: ":memory:" });
  await db.execute(`CREATE TABLE scan_snapshots (
    service TEXT NOT NULL,
    scan_time TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (service, scan_time))`);
  mockGetDb.mockReturnValue(db);
  mockReadFile.mockRejectedValue(ENOENT);
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  db.close();
  vi.restoreAllMocks();
});

describe("GET /api/equibles-short-crowding", () => {
  it("serves the latest Turso snapshot over an older disk cache", async () => {
    await insertSnapshot(buildPayload());
    mockReadFile.mockResolvedValue(
      JSON.stringify(
        buildPayload({ scan_time: new Date(Date.now() - 3_600_000).toISOString(), count: 1 }),
      ),
    );

    const json = await callRoute();
    expect(json.count).toBe(2);
    expect(json.missing).toBeUndefined();
    expect(json.latest_settlement_date).toBe(SETTLEMENT);
  });

  it("falls back to the disk cache when Turso has no rows", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload({ count: 9 })));
    const json = await callRoute();
    expect(json.count).toBe(9);
  });

  it("returns 200 with missing:true when nothing exists yet, never a 4xx", async () => {
    const json = await callRoute();
    expect(json).toEqual({
      missing: true,
      scan_time: null,
      count: 0,
      latest_settlement_date: null,
      entries: [],
      board: [],
    });
  });

  it("never lets a fresher entry-less snapshot mask an older populated one", async () => {
    const older = new Date(Date.now() - 3_600_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: older }), older);
    mockReadFile.mockResolvedValue(
      JSON.stringify(buildPayload({ scan_time: new Date().toISOString(), count: 0, entries: [] })),
    );

    const json = await callRoute();
    expect(json.count).toBe(2);
  });

  it("reports an entry-less snapshot as missing rather than an empty scoreboard", async () => {
    await insertSnapshot(buildPayload({ count: 0, entries: [] }));
    const json = await callRoute();
    expect(json.missing).toBe(true);
  });

  it("reads only this service's snapshots", async () => {
    await db.execute({
      sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('margin-debt', ?, ?)`,
      args: [new Date().toISOString(), JSON.stringify(buildPayload())],
    });
    const json = await callRoute();
    expect(json.missing).toBe(true);
  });

  it("degrades to the missing contract when the DB read throws", async () => {
    mockGetDb.mockImplementation(() => {
      throw new Error("libsql unavailable");
    });
    const json = await callRoute();
    expect(json.missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/equibles-short-crowding/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
