/**
 * @vitest-environment node
 *
 * /api/credit-vix — SHY minus HYG vs VIX (regime tab).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: mockGetDb,
}));

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
    source: "yahoo",
    count: 88,
    current: {
      date: "2026-09-29",
      shy_close: 81.16000366210938,
      hyg_close: 77.36000061035156,
      vix_close: 16.040000915527344,
      spread: 3.8000030517578125,
      rank_spread: 1,
      rank_vix: 0.146,
      gap: 0.854,
      state: "CREDIT WIDE",
      widest_since: "2015-08-17",
      window_sessions: 88,
    },
    series: [
      { date: "2026-09-28", shy_close: 81.0, hyg_close: 77.43, vix_close: 16.07, spread: 3.57 },
      { date: "2026-09-29", shy_close: 81.16000366210938, hyg_close: 77.36000061035156, vix_close: 16.040000915527344, spread: 3.8000030517578125 },
    ],
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload, scanTime?: string): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('credit-vix', ?, ?)`,
    args: [scanTime ?? (payload.scan_time as string), JSON.stringify(payload)],
  });
}


async function jsonOf(res: Response): Promise<Record<string, unknown>> {
  return (await res.json()) as Record<string, unknown>;
}

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

describe("GET /api/credit-vix", () => {
  it("serves the latest Turso snapshot (newest row wins)", async () => {
    await insertSnapshot(buildPayload());
    const staleTime = new Date(Date.now() - 60 * 60_000).toISOString();
    await insertSnapshot(buildPayload({ scan_time: staleTime, count: 1 }));

    const { GET } = await import("../app/api/credit-vix/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.count).toBe(88);
    expect((json.current as Payload).state).toBe("CREDIT WIDE");
    expect(json.missing).toBeUndefined();
  });

  it("serves the last good read from memory when Turso has no rows", async () => {
    await insertSnapshot(buildPayload({ count: 87 }));

    const { GET } = await import("../app/api/credit-vix/route");
    await GET();
    await db.execute("DELETE FROM scan_snapshots");
    const res = await GET();
    expect(res.status).toBe(200);
    expect((await jsonOf(res)).count).toBe(87);
  });

  it("returns the contract's missing:true shape with HTTP 200 when nothing exists", async () => {
    const { GET } = await import("../app/api/credit-vix/route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      missing: true,
      scan_time: null,
      source: null,
      count: 0,
      current: null,
      series: [],
    });
  });

  it("only reads the credit-vix service's snapshots", async () => {
    await db.execute({
      sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('iei-hyg', ?, ?)`,
      args: [new Date().toISOString(), JSON.stringify(buildPayload({ count: 5 }))],
    });

    const { GET } = await import("../app/api/credit-vix/route");
    const json = await jsonOf(await GET());
    expect(json.missing).toBe(true);
  });

  it("declares force-dynamic per the snapshot route cache contract", async () => {
    const route = await import("../app/api/credit-vix/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
