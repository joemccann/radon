/**
 * @vitest-environment node
 *
 * /api/equibles-ats-venue-share — the payload contract and the read cache.
 *
 * The writer stores a per-ticker `series` map (53.65 MB of a 54.2 MB row in
 * production) that no view reads. The route must keep it off the wire from
 * Turso AND out of the response, and coalesce reads through one in-process
 * cache so a weekly snapshot is not re-transferred on every browser miss.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createClient, type Client } from "@libsql/client";

let db: Client;
const executedRows: Array<Record<string, unknown>> = [];
let executeCount = 0;
const mockGetDb = vi.fn(() => ({
  execute: async (stmt: Parameters<Client["execute"]>[0]) => {
    executeCount += 1;
    const result = await db.execute(stmt);
    executedRows.push(...(result.rows as unknown as Array<Record<string, unknown>>));
    return result;
  },
}));
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

type Payload = Record<string, unknown>;

const SERIES_TICKERS = 200;
const SERIES_WEEKS = 52;

function row(ticker: string, week: string): Payload {
  return {
    ticker,
    week_start_date: week,
    ats_volume: 1_000_000,
    ats_share_pct: 42.5,
    short_volume_pct: 38.1,
    ats_share_z: 1.2,
    avg_ats_print_size_z: 0.4,
    classification: "accumulation",
  };
}

function buildSeries(): Record<string, Payload[]> {
  const series: Record<string, Payload[]> = {};
  for (let t = 0; t < SERIES_TICKERS; t += 1) {
    const ticker = `T${t}`;
    series[ticker] = Array.from({ length: SERIES_WEEKS }, (_, w) =>
      row(ticker, `2025-${String((w % 12) + 1).padStart(2, "0")}-01`));
  }
  return series;
}

function buildPayload(overrides: Payload = {}): Payload {
  return {
    scan_time: new Date().toISOString(),
    source: "finra-off-exchange-weekly",
    count: 2,
    tickers: ["AAPL", "NVDA"],
    week_start_date: "2026-08-03",
    thresholds: { accumulation_z: 1, distribution_z: -1, z_window_weeks: 52, min_history_weeks: 12 },
    current: [row("AAPL", "2026-08-03"), row("NVDA", "2026-08-03")],
    series: buildSeries(),
    errors: [],
    universe: { order: ["portfolio", "ndx100"] },
    ...overrides,
  };
}

async function insertSnapshot(payload: Payload): Promise<void> {
  await db.execute({
    sql: `INSERT INTO scan_snapshots (service, scan_time, payload) VALUES ('equibles-ats-venue-share', ?, ?)`,
    args: [payload.scan_time as string, JSON.stringify(payload)],
  });
}

const ENOENT = Object.assign(new Error("ENOENT: no such file"), { code: "ENOENT" });

async function getJson(): Promise<{ text: string; json: Payload }> {
  const { GET } = await import("../app/api/equibles-ats-venue-share/route");
  const res = await GET();
  expect(res.status).toBe(200);
  const text = await res.text();
  return { text, json: JSON.parse(text) as Payload };
}

beforeEach(async () => {
  vi.resetModules();
  executedRows.length = 0;
  executeCount = 0;
  db = createClient({ url: ":memory:" });
  await db.execute(`CREATE TABLE scan_snapshots (
    service TEXT NOT NULL, scan_time TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (service, scan_time))`);
  mockReadFile.mockReset();
  mockReadFile.mockRejectedValue(ENOENT);
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  db.close();
  vi.restoreAllMocks();
  vi.useRealTimers();
  delete process.env.RADON_DB_CACHE_FORCE;
});

describe("GET /api/equibles-ats-venue-share payload", () => {
  it("serves current and the panel's metadata without the unused series map", async () => {
    await insertSnapshot(buildPayload());
    const { json } = await getJson();
    expect(json).not.toHaveProperty("series");
    expect((json.current as Payload[]).map((r) => r.ticker)).toEqual(["AAPL", "NVDA"]);
    expect(json.week_start_date).toBe("2026-08-03");
    expect(json.thresholds).toMatchObject({ z_window_weeks: 52 });
    expect(json.universe).toEqual({ order: ["portfolio", "ndx100"] });
  });

  it("keeps the series map off the wire from Turso, not just out of the response", async () => {
    await insertSnapshot(buildPayload());
    await getJson();
    expect(executedRows).toHaveLength(1);
    expect(String(executedRows[0].payload)).not.toContain("\"series\"");
  });

  it("bounds the response to a fraction of the stored row", async () => {
    const stored = buildPayload();
    await insertSnapshot(stored);
    const { text } = await getJson();
    expect(text.length).toBeLessThan(JSON.stringify(stored).length / 50);
  });

  it("strips the series map from the disk fallback too", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload()));
    const { json } = await getJson();
    expect(json).not.toHaveProperty("series");
    expect(json.count).toBe(2);
  });

  it("keeps the missing:true contract with HTTP 200 when nothing exists", async () => {
    const { json } = await getJson();
    expect(json).toMatchObject({ missing: true, current: [], scan_time: null });
    expect(json).not.toHaveProperty("series");
  });
});

describe("GET /api/equibles-ats-venue-share read cache", () => {
  beforeEach(() => {
    process.env.RADON_DB_CACHE_FORCE = "1";
  });

  it("single-flights concurrent requests into one Turso read", async () => {
    await insertSnapshot(buildPayload());
    const { GET } = await import("../app/api/equibles-ats-venue-share/route");
    const responses = await Promise.all([GET(), GET(), GET()]);
    expect(responses.map((r) => r.status)).toEqual([200, 200, 200]);
    expect(executeCount).toBe(1);
  });

  it("serves from cache inside the TTL and rereads after it", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-08-04T12:00:00Z"));
    await insertSnapshot(buildPayload({ scan_time: "2026-08-04T11:00:00Z" }));
    const { GET } = await import("../app/api/equibles-ats-venue-share/route");

    await GET();
    vi.setSystemTime(new Date("2026-08-04T12:59:00Z"));
    await GET();
    expect(executeCount).toBe(1);

    vi.setSystemTime(new Date("2026-08-04T13:01:00Z"));
    await GET();
    expect(executeCount).toBe(2);
  });

  it("never caches an empty read, so the first real row is served immediately", async () => {
    const { GET } = await import("../app/api/equibles-ats-venue-share/route");
    const first = (await (await GET()).json()) as Payload;
    expect(first.missing).toBe(true);

    await insertSnapshot(buildPayload());
    const second = (await (await GET()).json()) as Payload;
    expect(second.missing).toBeUndefined();
    expect(second.count).toBe(2);
  });

  it("never caches a degraded snapshot with no current rows", async () => {
    await insertSnapshot(buildPayload({ current: [], count: 0, scan_time: "2026-01-01T00:00:00Z" }));
    const { GET } = await import("../app/api/equibles-ats-venue-share/route");
    await GET();
    await insertSnapshot(buildPayload());
    const json = (await (await GET()).json()) as Payload;
    expect(json.count).toBe(2);
  });

  it("parses the disk fallback once per TTL", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(buildPayload()));
    const { GET } = await import("../app/api/equibles-ats-venue-share/route");
    await GET();
    await GET();
    const snapshotReads = mockReadFile.mock.calls
      .filter(([path]) => String(path).endsWith("equibles_ats_venue_share.json"));
    expect(snapshotReads).toHaveLength(1);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    const route = await import("../app/api/equibles-ats-venue-share/route");
    expect(route.dynamic).toBe("force-dynamic");
    expect(route.radonCapability).toBe("read");
  });
});
