import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * GET /api/gex must serve whichever store (Turso DB vs disk) has the NEWER
 * scan_time. Reading the DB unconditionally first hid fresh disk data behind a
 * stale Turso row — GEX showed a week-old 2026-06-16 snapshot while the VPS
 * data/gex.json was already current (2026-06-23). Bug fixed 2026-06-23.
 */
const mockGetDb = vi.fn();
const mockReadFile = vi.fn();
const mockRadonFetch = vi.fn();

vi.mock("fs/promises", () => ({ readFile: mockReadFile }));
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => mockGetDb() }));
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

function dbReturning(payload: Record<string, unknown> | null) {
  return {
    execute: async () => ({ rows: payload ? [{ payload: JSON.stringify(payload) }] : [] }),
  };
}

beforeEach(() => {
  vi.resetModules();
  mockGetDb.mockReset();
  mockReadFile.mockReset();
  mockRadonFetch.mockReset().mockResolvedValue({});
});

describe("GET /api/gex prefers the freshest store", () => {
  it("serves the disk snapshot when it is newer than the DB row", async () => {
    mockGetDb.mockReturnValue(dbReturning({ ticker: "SPX", scan_time: "2026-06-16T23:39:09Z", spot: 7500 }));
    mockReadFile.mockResolvedValue(
      JSON.stringify({ ticker: "SPX", scan_time: "2026-06-23T13:00:00Z", spot: 7600, data_date: "2026-06-23" }),
    );
    const { GET } = await import("../app/api/gex/route");
    const body = await (await GET()).json();
    expect(body.scan_time).toBe("2026-06-23T13:00:00Z");
  });

  it("keeps the DB row when it is newer than disk", async () => {
    mockGetDb.mockReturnValue(dbReturning({ ticker: "SPX", scan_time: "2026-06-23T14:00:00Z", spot: 7700 }));
    mockReadFile.mockResolvedValue(
      JSON.stringify({ ticker: "SPX", scan_time: "2026-06-23T13:00:00Z", spot: 7600 }),
    );
    const { GET } = await import("../app/api/gex/route");
    const body = await (await GET()).json();
    expect(body.scan_time).toBe("2026-06-23T14:00:00Z");
  });

  it("falls back to disk when the DB has no row", async () => {
    mockGetDb.mockReturnValue(dbReturning(null));
    mockReadFile.mockResolvedValue(
      JSON.stringify({ ticker: "SPX", scan_time: "2026-06-23T13:00:00Z", spot: 7600 }),
    );
    const { GET } = await import("../app/api/gex/route");
    const body = await (await GET()).json();
    expect(body.scan_time).toBe("2026-06-23T13:00:00Z");
  });
});
