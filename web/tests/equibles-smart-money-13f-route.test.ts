/**
 * @vitest-environment node
 *
 * /api/equibles-smart-money-13f — per-ticker 13F depth read route.
 *
 * GET-only: 13F is quarterly, so the refresh job writes and this route only
 * reads. Turso-first through the dbFirstRead chokepoint (newest vintage in
 * equibles_13f_snapshots) with a data/equibles_13f/{TICKER}.json fallback.
 * Absent data is the contract's `missing: true` shape at HTTP 200; only a
 * malformed/absent ticker is a real client error and 400s.
 *
 * Split from the jsdom panel suite deliberately: under the jsdom environment
 * the fs/promises mock does not reach a dynamically re-imported route module,
 * so the disk-fallback branch silently reads the real filesystem. Same split
 * as margin-debt-api.test.ts vs margin-debt-panel.test.tsx.
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
  return { ...actual, readFile: (...args: unknown[]) => mockReadFile(...args) };
});

const ENOENT = Object.assign(new Error("ENOENT: no such file"), { code: "ENOENT" });

type Payload = Record<string, unknown>;

function buildPayload(overrides: Payload = {}): Payload {
  return {
    ticker: "AAPL",
    scan_time: new Date().toISOString(),
    report_date: "2026-06-30",
    disclosure: {
      report_date: "2026-06-30",
      quarter: "Q2 2026",
      filed_by: "2026-08-14",
      age_days: 42,
      staleness: "fresh",
      cadence: "quarterly",
      lag_days: 45,
      context_only: true,
      gate_note: "Fails Gate 2 on its own.",
    },
    headline: {
      filer_count: 5240,
      filer_count_delta: 140,
      new_positions: 412,
      exited_positions: 188,
      ownership_pct: 61.4,
      top_holders: [],
      super_investors: [],
    },
    holders: [],
    holder_count: 3,
    ownership_series: [],
    activity: { buyers: [], sellers: [] },
    super_investors: [],
    market_context: null,
    ...overrides,
  };
}

async function seedSchema(client: Client): Promise<void> {
  await client.execute(`CREATE TABLE equibles_13f_snapshots (
    ticker TEXT NOT NULL,
    report_date TEXT NOT NULL,
    scan_time TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (ticker, report_date))`);
}

async function insertSnapshot(ticker: string, reportDate: string, payload: Payload): Promise<void> {
  await db.execute({
    sql: `INSERT INTO equibles_13f_snapshots (ticker, report_date, scan_time, payload)
          VALUES (?, ?, ?, ?)`,
    args: [ticker, reportDate, payload.scan_time as string, JSON.stringify(payload)],
  });
}

function request(query: string): Request {
  return new Request(`http://localhost:3000/api/equibles-smart-money-13f${query}`);
}

async function jsonOf(res: Response): Promise<Payload> {
  return (await res.json()) as Payload;
}

beforeEach(async () => {
  vi.resetModules();
  db = createClient({ url: ":memory:" });
  await seedSchema(db);
  mockGetDb.mockReturnValue(db);
  mockReadFile.mockReset();
  mockReadFile.mockRejectedValue(ENOENT);
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  db.close();
  vi.restoreAllMocks();
});

describe("GET /api/equibles-smart-money-13f", () => {
  it("serves the newest vintage for the ticker", async () => {
    await insertSnapshot("AAPL", "2026-03-31", buildPayload({ report_date: "2026-03-31", holder_count: 1 }));
    await insertSnapshot("AAPL", "2026-06-30", buildPayload());

    const { GET } = await import("../app/api/equibles-smart-money-13f/route");
    const res = await GET(request("?ticker=AAPL"));
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.report_date).toBe("2026-06-30");
    expect(json.holder_count).toBe(3);
    expect(json.missing).toBeUndefined();
  });

  it("scopes the read to the requested ticker", async () => {
    await insertSnapshot("MSFT", "2026-06-30", buildPayload({ ticker: "MSFT" }));

    const { GET } = await import("../app/api/equibles-smart-money-13f/route");
    const json = await jsonOf(await GET(request("?ticker=AAPL")));
    expect(json.missing).toBe(true);
    expect(json.ticker).toBe("AAPL");
  });

  it("falls back to the per-ticker disk cache when Turso has no row", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload({ holder_count: 99 })));

    const { GET } = await import("../app/api/equibles-smart-money-13f/route");
    const json = await jsonOf(await GET(request("?ticker=AAPL")));
    expect(json.holder_count).toBe(99);
  });

  it("returns 200 with missing:true when no data exists yet, never a 4xx", async () => {
    const { GET } = await import("../app/api/equibles-smart-money-13f/route");
    const res = await GET(request("?ticker=AAPL"));
    expect(res.status).toBe(200);
    expect(await jsonOf(res)).toEqual({
      ticker: "AAPL",
      missing: true,
      scan_time: null,
      report_date: null,
      disclosure: null,
      headline: null,
      holders: [],
      holder_count: 0,
      ownership_series: [],
      activity: { buyers: [], sellers: [] },
      super_investors: [],
      market_context: null,
    });
  });

  it("400s only for a genuinely invalid request", async () => {
    const { GET } = await import("../app/api/equibles-smart-money-13f/route");
    expect((await GET(request(""))).status).toBe(400);
    expect((await GET(request("?ticker=NOT_A_TICKER_123"))).status).toBe(400);
  });

  it("normalizes a lowercase ticker", async () => {
    await insertSnapshot("AAPL", "2026-06-30", buildPayload());

    const { GET } = await import("../app/api/equibles-smart-money-13f/route");
    const json = await jsonOf(await GET(request("?ticker=aapl")));
    expect(json.report_date).toBe("2026-06-30");
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/equibles-smart-money-13f/route");
    expect(route.dynamic).toBe("force-dynamic");
  });
});
