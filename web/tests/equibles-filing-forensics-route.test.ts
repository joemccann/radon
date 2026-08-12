/**
 * @vitest-environment node
 *
 * GET /api/equibles-filing-forensics — the pre-trade filing-forensics read route.
 *
 * Turso-first through dbFirstRead with a per-ticker disk fallback. Absent data
 * is HTTP 200 + missing:true; only a malformed ticker is a 4xx. Split from
 * equibles-filing-forensics.test.tsx (jsdom, the surface) because the disk
 * fallback needs a node environment: under jsdom the route's `fs/promises`
 * import is not interceptable by vi.mock, so the fallback silently never fires.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

type Row = Record<string, unknown>;

const dbRows: { value: Row[] } = { value: [] };
const dbExecute = vi.fn(async () => ({ rows: dbRows.value }));
vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: () => ({ execute: dbExecute }),
}));

const mockReadFile = vi.fn();
vi.mock("fs/promises", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs/promises")>();
  return { ...actual, readFile: (...args: unknown[]) => mockReadFile(...args) };
});

const ENOENT = Object.assign(new Error("ENOENT: no such file"), { code: "ENOENT" });

function check(overrides: Row = {}): Row {
  return {
    code: "dilution_overhang",
    label: "Dilution overhang",
    gate: 3,
    tone: "adverse",
    verdict: "flagged",
    detail: "$45.0M of unused at-the-market shelf capacity.",
    figure: { value: 45_000_000, unit: "usd", label: "UNUSED ATM CAPACITY" },
    filing: { form: "424B5", filed_at: "2026-07-02", url: "https://sec.gov/atm.htm" },
    ...overrides,
  };
}

function dossier(overrides: Row = {}): Row {
  return {
    ticker: "AAPL",
    as_of: new Date().toISOString(),
    data_complete: true,
    flag_count: 1,
    flags: [check()],
    checks: [check()],
    sources: { atm_programs: { status: "ok", count: 1, error: null } },
    ...overrides,
  };
}

async function jsonOf(res: Response): Promise<Row> {
  return (await res.json()) as Row;
}

async function loadRoute() {
  return import("../app/api/equibles-filing-forensics/route");
}

function request(query = ""): Request {
  return new Request(`http://localhost/api/equibles-filing-forensics${query}`);
}

beforeEach(() => {
  vi.resetModules();
  dbRows.value = [];
  dbExecute.mockClear();
  mockReadFile.mockRejectedValue(ENOENT);
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /api/equibles-filing-forensics", () => {
  it("serves the Turso dossier for a ticker", async () => {
    const payload = dossier();
    dbRows.value = [{ as_of: payload.as_of, payload: JSON.stringify(payload) }];

    const { GET } = await loadRoute();
    const res = await GET(request("?ticker=AAPL"));
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.ticker).toBe("AAPL");
    expect(json.flag_count).toBe(1);
    expect(json.missing).toBeUndefined();
  });

  it("falls back to the on-disk dossier when Turso has no row", async () => {
    mockReadFile.mockResolvedValue(JSON.stringify(dossier({ flag_count: 3 })));

    const { GET } = await loadRoute();
    const json = await jsonOf(await GET(request("?ticker=AAPL")));
    expect(json.flag_count).toBe(3);
  });

  it("returns 200 with missing:true when no dossier exists anywhere", async () => {
    const { GET } = await loadRoute();
    const res = await GET(request("?ticker=ZZZZ"));
    expect(res.status).toBe(200);
    const json = await jsonOf(res);
    expect(json.missing).toBe(true);
    expect(json.ticker).toBe("ZZZZ");
    expect(json.checks).toEqual([]);
  });

  it("never reports an absent dossier as data_complete", async () => {
    const { GET } = await loadRoute();
    const json = await jsonOf(await GET(request("?ticker=ZZZZ")));
    expect(json.data_complete).toBe(false);
  });

  it("marks a dossier older than the refresh budget stale", async () => {
    const old = new Date(Date.now() - 5 * 24 * 60 * 60_000).toISOString();
    dbRows.value = [{ as_of: old, payload: JSON.stringify(dossier({ as_of: old })) }];

    const { GET } = await loadRoute();
    const json = await jsonOf(await GET(request("?ticker=AAPL")));
    expect(json.stale).toBe(true);
  });

  it("keeps a fresh dossier unmarked", async () => {
    const payload = dossier();
    dbRows.value = [{ as_of: payload.as_of, payload: JSON.stringify(payload) }];

    const { GET } = await loadRoute();
    const json = await jsonOf(await GET(request("?ticker=AAPL")));
    expect(json.stale).toBe(false);
  });

  it("rejects a malformed ticker with a 4xx (a real client error)", async () => {
    const { GET } = await loadRoute();
    const res = await GET(request("?ticker=not-a-ticker!"));
    expect(res.status).toBe(400);
  });

  it("lists flagged tickers worst first when no ticker is given", async () => {
    dbRows.value = [
      { ticker: "BBB", payload: JSON.stringify(dossier({ ticker: "BBB", flag_count: 3 })) },
      { ticker: "AAA", payload: JSON.stringify(dossier({ ticker: "AAA", flag_count: 1 })) },
    ];

    const { GET } = await loadRoute();
    const json = await jsonOf(await GET(request()));
    expect((json.dossiers as Row[]).map((d) => d.ticker)).toEqual(["BBB", "AAA"]);
    expect(dbExecute.mock.calls[0][0]).toMatchObject({
      sql: expect.stringContaining("flag_count > 0"),
    });
  });

  it("degrades the flagged listing to 200 + missing when the DB fails", async () => {
    dbExecute.mockRejectedValueOnce(new Error("libsql down"));
    const { GET } = await loadRoute();
    const res = await GET(request());
    expect(res.status).toBe(200);
    expect((await jsonOf(res)).missing).toBe(true);
  });

  it("declares force-dynamic per the disk-backed route cache contract", async () => {
    expect((await loadRoute()).dynamic).toBe("force-dynamic");
  });
});
