/**
 * @vitest-environment node
 *
 * /api/options/rv-ratio — realized-vol-ratio snapshot route.
 *
 * GET reads through the dbFirstRead chokepoint (Turso rv_ratio_snapshots
 * row first, data/rv_ratio/{SYM}.json fallback) and attaches a
 * SESSION-RELATIVE `stale` flag: a snapshot is current iff its
 * coverage.last_date covers the last completed ET session, so Saturday
 * reading Friday's scan is fresh despite 20+ wall-clock hours. Both
 * sources absent is HTTP 200 missing:true, never a 4xx.
 *
 * POST proxies FastAPI's synchronous scan endpoint via radonFetch with the
 * RV_RATIO_SCAN_TIMEOUT_MS budget and maps failures through
 * optionsErrorResponse (timeout -> 504, RadonApiError -> passthrough).
 *
 * Mechanics mirror margin-debt-api.test.ts: @/lib/db backed by a real
 * in-memory libsql client, fs reads mocked, radonFetch mocked. The
 * market-session source of truth is mocked so weekend/holiday semantics
 * are deterministic; every date is derived from the real clock
 * (window-relative), never hardcoded.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";

import {
  addCalendarDays,
  buildPartialRvRatioFixture,
  weekdayOf,
} from "./rv-ratio-fixture";

const mocks = vi.hoisted(() => ({
  radonFetch: vi.fn(),
  lastCompletedSessionDate: vi.fn<() => string>(),
}));

vi.mock("@/lib/radonApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/radonApi")>();
  return { ...actual, radonFetch: mocks.radonFetch };
});

vi.mock("@/lib/marketSession", () => ({
  // The fixture's default endDate and the route's staleness check both pin
  // to the same mocked session so weekend/holiday semantics are deterministic.
  mostRecentSessionDate: () => mocks.lastCompletedSessionDate(),
  lastCompletedSessionDate: () => mocks.lastCompletedSessionDate(),
}));

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

// Window-relative weekend anchor: the most recent Saturday in ET, its
// Friday session, and the Friday one week earlier.
const todayEt = new Date().toLocaleDateString("sv", { timeZone: "America/New_York" });
let saturday = todayEt;
while (weekdayOf(saturday) !== 6) saturday = addCalendarDays(saturday, -1);
const fridaySession = addCalendarDays(saturday, -1);
const previousFridaySession = addCalendarDays(fridaySession, -7);

const ENOENT = Object.assign(new Error("ENOENT: no such file"), { code: "ENOENT" });

type Payload = Record<string, unknown>;

async function seedSchema(client: Client): Promise<void> {
  await client.execute(`CREATE TABLE rv_ratio_snapshots (
    symbol   TEXT PRIMARY KEY,
    taken_at TEXT NOT NULL,
    payload  TEXT NOT NULL)`);
}

async function insertSnapshot(payload: Payload, takenAt: string): Promise<void> {
  await db.execute({
    sql: `INSERT OR REPLACE INTO rv_ratio_snapshots (symbol, taken_at, payload) VALUES (?, ?, ?)`,
    args: [payload.symbol as string, takenAt, JSON.stringify(payload)],
  });
}

async function jsonOf(res: Response): Promise<Payload> {
  return (await res.json()) as Payload;
}

beforeEach(async () => {
  vi.resetModules();
  mocks.radonFetch.mockReset();
  // Simulate a weekend read: the last completed session is Friday.
  mocks.lastCompletedSessionDate.mockReturnValue(fridaySession);
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

describe("GET /api/options/rv-ratio", () => {
  it.each([
    ["http://localhost/api/options/rv-ratio", "Required: symbol"],
    ["http://localhost/api/options/rv-ratio?symbol=SMH%24", "Invalid symbol"],
  ])("rejects malformed requests: %s", async (url, expectedError) => {
    const { GET } = await import("../app/api/options/rv-ratio/route");
    const response = await GET(new Request(url));

    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ error: expectedError, code: "BAD_REQUEST" });
  });

  it("returns 200 missing:true with no-store headers when both sources are absent", async () => {
    const { GET } = await import("../app/api/options/rv-ratio/route");
    const response = await GET(new Request("http://localhost/api/options/rv-ratio?symbol=SMH"));

    expect(response.status).toBe(200);
    expect((response.headers.get("Cache-Control") ?? "").toLowerCase()).toContain("no-store");
    expect(await response.json()).toEqual({ schema_version: 1, symbol: "SMH", missing: true });
  });

  it("normalizes the symbol and serves the newer DB snapshot over an older disk cache", async () => {
    const dbPayload = buildPartialRvRatioFixture({ endDate: fridaySession });
    await insertSnapshot(dbPayload, `${fridaySession}T21:05:00Z`);
    const diskPayload = {
      ...buildPartialRvRatioFixture({ endDate: previousFridaySession }),
      scan_time: `${previousFridaySession}T21:05:00Z`,
    };
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload));

    const { GET } = await import("../app/api/options/rv-ratio/route");
    const response = await GET(new Request("http://localhost/api/options/rv-ratio?symbol=smh"));

    expect(response.status).toBe(200);
    const json = await jsonOf(response);
    expect((json.coverage as Payload).last_date).toBe(fridaySession);
    expect(json.stale).toBe(false);
  });

  it("serves the disk snapshot when it is newer than the DB row", async () => {
    const dbPayload = buildPartialRvRatioFixture({ endDate: previousFridaySession });
    await insertSnapshot(dbPayload, `${previousFridaySession}T21:05:00Z`);
    const diskPayload = buildPartialRvRatioFixture({ endDate: fridaySession });
    mockReadFile.mockResolvedValue(JSON.stringify(diskPayload));

    const { GET } = await import("../app/api/options/rv-ratio/route");
    const response = await GET(new Request("http://localhost/api/options/rv-ratio?symbol=SMH"));

    const json = await jsonOf(response);
    expect((json.coverage as Payload).last_date).toBe(fridaySession);
    expect(mockReadFile).toHaveBeenCalledWith(
      expect.stringContaining(`rv_ratio/SMH.json`),
      "utf-8",
    );
  });

  it("marks Friday's snapshot fresh on a weekend read (session rule, not wall clock)", async () => {
    const payload = buildPartialRvRatioFixture({ endDate: fridaySession });
    await insertSnapshot(payload, `${fridaySession}T21:05:00Z`);

    const { GET } = await import("../app/api/options/rv-ratio/route");
    const json = await jsonOf(
      await GET(new Request("http://localhost/api/options/rv-ratio?symbol=SMH")),
    );

    expect(json.stale).toBe(false);
  });

  it("marks a snapshot behind the last completed session stale", async () => {
    const payload = buildPartialRvRatioFixture({ endDate: previousFridaySession });
    await insertSnapshot(payload, `${previousFridaySession}T21:05:00Z`);

    const { GET } = await import("../app/api/options/rv-ratio/route");
    const json = await jsonOf(
      await GET(new Request("http://localhost/api/options/rv-ratio?symbol=SMH")),
    );

    expect(json.stale).toBe(true);
  });

  it("treats a holiday-shortened week correctly: session SoT decides, not the weekday", async () => {
    // Holiday Friday: the market-session SoT reports Thursday as the last
    // completed session, so Thursday's snapshot is fresh.
    const thursdaySession = addCalendarDays(fridaySession, -1);
    mocks.lastCompletedSessionDate.mockReturnValue(thursdaySession);
    const payload = buildPartialRvRatioFixture({ endDate: thursdaySession });
    await insertSnapshot(payload, `${thursdaySession}T21:05:00Z`);

    const { GET } = await import("../app/api/options/rv-ratio/route");
    const json = await jsonOf(
      await GET(new Request("http://localhost/api/options/rv-ratio?symbol=SMH")),
    );

    expect(json.stale).toBe(false);
  });
});

describe("POST /api/options/rv-ratio", () => {
  it("rejects malformed symbols before FastAPI", async () => {
    const { POST } = await import("../app/api/options/rv-ratio/route");
    const response = await POST(
      new Request("http://localhost/api/options/rv-ratio?symbol=SMH%24", { method: "POST" }),
    );

    expect(response.status).toBe(400);
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("proxies the scan synchronously and returns the fresh payload with staleness attached", async () => {
    const fresh = buildPartialRvRatioFixture({ endDate: fridaySession });
    mocks.radonFetch.mockResolvedValue(fresh);

    const { POST } = await import("../app/api/options/rv-ratio/route");
    const response = await POST(
      new Request("http://localhost/api/options/rv-ratio?symbol=smh", { method: "POST" }),
    );

    expect(response.status).toBe(200);
    expect((response.headers.get("Cache-Control") ?? "").toLowerCase()).toContain("no-store");
    const json = await jsonOf(response);
    expect(json.symbol).toBe("SMH");
    expect(json.stale).toBe(false);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/options/rv-ratio/SMH/scan",
      { method: "POST", timeout: 250_000 },
    );
  });

  it("passes the cooldown variant through untouched", async () => {
    mocks.radonFetch.mockResolvedValue({ status: "cooldown", retry_in: 412 });

    const { POST } = await import("../app/api/options/rv-ratio/route");
    const response = await POST(
      new Request("http://localhost/api/options/rv-ratio?symbol=SMH", { method: "POST" }),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "cooldown", retry_in: 412 });
  });

  it("maps scan timeouts to 504", async () => {
    mocks.radonFetch.mockRejectedValue(new Error("request timed out"));

    const { POST } = await import("../app/api/options/rv-ratio/route");
    const response = await POST(
      new Request("http://localhost/api/options/rv-ratio?symbol=SMH", { method: "POST" }),
    );

    expect(response.status).toBe(504);
    expect(await response.json()).toMatchObject({ code: "UPSTREAM_TIMEOUT" });
  });

  it("preserves FastAPI error status and detail", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mocks.radonFetch.mockRejectedValue(new RadonApiError(503, "rv_ratio_scan.py failed"));

    const { POST } = await import("../app/api/options/rv-ratio/route");
    const response = await POST(
      new Request("http://localhost/api/options/rv-ratio?symbol=SMH", { method: "POST" }),
    );

    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({
      error: "RV ratio scan unavailable",
      detail: "rv_ratio_scan.py failed",
      code: "UPSTREAM_ERROR",
    });
  });
});

describe("route cache contract", () => {
  it("declares force-dynamic and the nodejs runtime", async () => {
    const route = await import("../app/api/options/rv-ratio/route");
    expect(route.dynamic).toBe("force-dynamic");
    expect(route.runtime).toBe("nodejs");
  });
});
