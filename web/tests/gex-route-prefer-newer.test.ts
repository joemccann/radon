import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * GET /api/gex reads Turso and nothing else. When Turso has no row or fails, the
 * last good read serves from memory, carrying its own scan_time so the route's
 * stale verdict still fires.
 */
const mockGetDb = vi.fn();
const mockRadonFetch = vi.fn();

vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: () => mockGetDb() }));
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch, RadonApiError: class extends Error {} }));

function dbReturning(payload: Record<string, unknown> | null) {
  return {
    execute: async () => ({ rows: payload ? [{ payload: JSON.stringify(payload) }] : [] }),
  };
}

beforeEach(() => {
  vi.resetModules();
  mockGetDb.mockReset();
  mockRadonFetch.mockReset().mockResolvedValue({});
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("GET /api/gex reads Turso, then memory", () => {
  const SNAPSHOT = { ticker: "SPX", scan_time: "2026-06-23T14:00:00Z", spot: 7700 };

  it("serves the DB row", async () => {
    mockGetDb.mockReturnValue(dbReturning(SNAPSHOT));
    const { GET } = await import("../app/api/gex/route");
    const body = await (await GET()).json();
    expect(body.scan_time).toBe(SNAPSHOT.scan_time);
  });

  it("serves the last good read and reports it stale when Turso later has no row", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-06-23T14:30:00Z"));
    try {
      mockGetDb.mockReturnValue(dbReturning(SNAPSHOT));
      const { POST } = await import("../app/api/gex/route");
      // POST's failure path bypasses the 5s GET coalescing window.
      mockRadonFetch.mockRejectedValue(new Error("upstream down"));
      await POST();
      mockGetDb.mockReturnValue(dbReturning(null));
      const body = await (await POST()).json();
      expect(body.spot).toBe(7700);
      expect(body.is_stale).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("is empty when Turso has never had a row", async () => {
    mockGetDb.mockReturnValue(dbReturning(null));
    const { GET } = await import("../app/api/gex/route");
    const body = await (await GET()).json();
    expect(body.scan_time).toBe("");
    expect(body.is_stale).toBe(true);
  });
});
