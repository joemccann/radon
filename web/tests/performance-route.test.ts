import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockReadFile = vi.fn();
const mockStat = vi.fn();

vi.mock("fs/promises", () => ({
  readFile: mockReadFile,
  stat: mockStat,
}));

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({ radonFetch: mockRadonFetch }));

const mockGetDb = vi.fn();
vi.mock("@/lib/db", () => ({ resetDb: () => {}, getDb: mockGetDb }));

function mockDbSnapshots({
  performance = null,
  portfolio = null,
}: {
  performance?: Record<string, unknown> | null;
  portfolio?: Record<string, unknown> | null;
} = {}) {
  mockGetDb.mockReturnValue({
    execute: vi.fn(async ({ sql }: { sql: string }) => {
      if (/FROM\s+performance_snapshots/i.test(sql)) {
        return {
          rows: performance
            ? [
                {
                  taken_at: String(performance.last_sync ?? performance.as_of ?? "2026-03-13T12:00:00Z"),
                  payload: JSON.stringify(performance),
                },
              ]
            : [],
        };
      }
      if (/FROM\s+portfolio_snapshots/i.test(sql)) {
        return {
          rows: portfolio ? [{ payload: JSON.stringify(portfolio) }] : [],
        };
      }
      return { rows: [] };
    }),
  });
}

describe("/api/performance route", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-13T16:10:00Z"));
    vi.clearAllMocks();
    mockReadFile.mockReset();
    mockStat.mockReset();
    mockRadonFetch.mockReset();
    mockGetDb.mockReset();
    mockDbSnapshots();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("GET returns cached performance data when cache is fresh and aligned with portfolio", async () => {
    mockDbSnapshots({
      portfolio: {
        last_sync: "2026-03-13T16:08:00Z",
        account_summary: { net_liquidation: 1_313_112.03 },
      },
    });
    mockStat.mockResolvedValue({ mtimeMs: Date.now() });
    mockReadFile.mockImplementation(async (path: string) => {
      if (path.includes("performance.json")) {
        return JSON.stringify({
          as_of: "2026-03-13",
          // Inside the 5-min market-open TTL of the fake clock (16:10Z).
          last_sync: "2026-03-13T16:08:00Z",
          summary: { sharpe_ratio: 1.2 },
          series: [],
        });
      }
      throw new Error(`unexpected read: ${path}`);
    });

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.as_of).toBe("2026-03-13");
    expect(body.summary.sharpe_ratio).toBe(1.2);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("GET returns stale cache + triggers background rebuild when cached performance lags the current portfolio snapshot (SWR)", async () => {
    mockDbSnapshots({
      portfolio: {
        last_sync: "2026-03-11T13:37:14Z",
        account_summary: { net_liquidation: 1_313_112.03 },
      },
    });
    mockStat.mockResolvedValue({ mtimeMs: Date.now() });
    mockReadFile.mockImplementation(async (path: string) => {
      if (path.includes("performance.json")) {
        return JSON.stringify({
          as_of: "2026-03-10",
          last_sync: "2026-03-10T18:55:00Z",
          summary: { ending_equity: 1_063_031.86 },
          series: [],
        });
      }
      throw new Error(`unexpected read: ${path}`);
    });
    mockRadonFetch.mockResolvedValueOnce({ status: "accepted" });

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    // SWR: returns stale cache immediately
    expect(res.status).toBe(200);
    expect(body.as_of).toBe("2026-03-10");
    expect(body.summary.ending_equity).toBe(1_063_031.86);
    // §4.4: no blocking /portfolio/sync — one fire-and-forget rebuild only.
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("GET returns stale cache + triggers background rebuild when perf is behind current ET session (SWR)", async () => {
    mockDbSnapshots({
      portfolio: {
        last_sync: "2026-03-12T13:23:21Z",
        account_summary: { net_liquidation: 1_218_410.03 },
      },
    });
    mockStat.mockResolvedValue({ mtimeMs: Date.now() });
    mockReadFile.mockImplementation(async (path: string) => {
      if (path.includes("performance.json")) {
        return JSON.stringify({
          as_of: "2026-03-12",
          last_sync: "2026-03-12T13:23:21Z",
          summary: { ending_equity: 1_218_410.03 },
          series: [],
        });
      }
      throw new Error(`unexpected read: ${path}`);
    });
    mockRadonFetch.mockResolvedValueOnce({ status: "accepted" });

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    // SWR: returns stale cache immediately
    expect(res.status).toBe(200);
    expect(body.as_of).toBe("2026-03-12");
    expect(body.summary.ending_equity).toBe(1_218_410.03);
    // §4.4: no blocking /portfolio/sync — one fire-and-forget rebuild only.
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("GET serves cached performance even when the background rebuild trigger fails", async () => {
    mockDbSnapshots({
      portfolio: {
        last_sync: "2026-03-12T13:23:21Z",
        account_summary: { net_liquidation: 1_218_410.03 },
      },
    });
    mockStat.mockResolvedValue({ mtimeMs: Date.now() });
    mockReadFile.mockImplementation(async (path: string) => {
      if (path.includes("performance.json")) {
        return JSON.stringify({
          as_of: "2026-03-12",
          last_sync: "2026-03-12T13:23:21Z",
          summary: { ending_equity: 1_218_410.03 },
          series: [],
        });
      }
      throw new Error(`unexpected read: ${path}`);
    });
    mockRadonFetch.mockRejectedValue(new Error("IB unavailable"));

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.as_of).toBe("2026-03-12");
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("POST runs the API sync and returns generated performance JSON", async () => {
    const payload = {
      as_of: "2026-03-10",
      last_sync: "2026-03-10T18:55:00Z",
      summary: { sharpe_ratio: 1.84 },
      series: [{ date: "2026-01-02", equity: 1_000_000 }],
    };
    mockRadonFetch.mockResolvedValue(payload);

    const { POST } = await import("../app/api/performance/route");
    const res = await POST();
    const body = await res.json();

    expect(res.status).toBe(404);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  // ---- SWR-specific tests ----

  it("GET SWR: returns stale cache immediately and triggers background rebuild", async () => {
    mockDbSnapshots({ portfolio: { last_sync: "2026-03-13T12:00:00Z" } });
    // Stale: mtime is 20 minutes ago
    mockStat.mockResolvedValue({ mtimeMs: Date.now() - 20 * 60_000 });
    mockReadFile.mockImplementation(async (path: string) => {
      if (path.includes("performance.json")) {
        return JSON.stringify({
          as_of: "2026-03-13",
          last_sync: "2026-03-13T12:00:00Z",
          summary: { sharpe_ratio: 1.2 },
          series: [],
        });
      }
      throw new Error(`unexpected read: ${path}`);
    });
    // Background trigger should fire-and-forget
    mockRadonFetch.mockResolvedValue({ status: "accepted" });

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.summary.sharpe_ratio).toBe(1.2);
    // Should call background endpoint, not the blocking one
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("GET cold start: blocks on rebuild when no cache exists", async () => {
    mockStat.mockRejectedValue(new Error("ENOENT"));
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    mockRadonFetch.mockResolvedValue({
      as_of: "2026-03-13",
      last_sync: "2026-03-13T16:00:00Z",
      summary: { total_return: 0.18 },
      series: [],
    });

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.status).toBe("unavailable");
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("GET cold start: returns 200 with status unavailable when rebuild fails and no cache", async () => {
    // §C.6: missing data is a status, never a 4xx or 5xx. The UI branches on
    // `status` rather than guessing at an error envelope. The upstream error
    // text must still never reach the client.
    mockStat.mockRejectedValue(new Error("ENOENT"));
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    mockRadonFetch.mockRejectedValue(new Error("FastAPI down"));

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.status).toBe("unavailable");
    expect(body.warnings.map((w: { code: string }) => w.code)).toContain("NAV_UNAVAILABLE");
    expect(JSON.stringify(body)).not.toContain("FastAPI down");
  });

  it("GET SWR: background trigger failure is swallowed — stale cache still returned", async () => {
    mockDbSnapshots({ portfolio: { last_sync: "2026-03-13T12:00:00Z" } });
    mockStat.mockResolvedValue({ mtimeMs: Date.now() - 20 * 60_000 });
    mockReadFile.mockImplementation(async (path: string) => {
      if (path.includes("performance.json")) {
        return JSON.stringify({
          as_of: "2026-03-13",
          last_sync: "2026-03-13T12:00:00Z",
          summary: { ending_equity: 100_000 },
          series: [],
        });
      }
      throw new Error("not found");
    });
    mockRadonFetch.mockRejectedValue(new Error("timeout"));

    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.summary.ending_equity).toBe(100_000);
  });
});

/* ── R-346 / REL-126(b): the route serves staleness, not just computes it ───
 *
 * `stale` and `shouldRebuild` were computed and DISCARDED: the
 * `!shouldRebuild` branch and the `cachedPerformance` branch returned the
 * byte-identical response, and `triggerBackgroundRebuild` was an empty
 * function. A payload three days past its 60-minute CLOSED TTL was served
 * with no stale flag, no header and nothing to trigger a refresh. The
 * payload's own honesty markers do not cover this: `nav_sessions_behind` and
 * every NAV_STALE warning are frozen at BUILD time, so a payload built Friday
 * still reads ok / 0 on Monday.
 */
describe("/api/performance staleness is served", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-16T22:00:00Z")); // Monday, market closed
    vi.clearAllMocks();
    mockReadFile.mockReset();
    mockStat.mockReset();
    mockRadonFetch.mockReset();
    mockGetDb.mockReset();
  });

  afterEach(() => vi.useRealTimers());

  const PAYLOAD = {
    as_of: "2026-03-13",
    last_sync: "2026-03-13T20:00:00Z",
    generated_at: "2026-03-13T20:00:00Z",
    status: "ok",
    nav_sessions_behind: 0,
    warnings: [],
  };

  it("flags a payload three days past its TTL as stale", async () => {
    mockDbSnapshots({ performance: PAYLOAD });
    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = (await res.json()) as Record<string, unknown>;

    expect(body.stale).toBe(true);
    expect(res.headers.get("X-Radon-Stale")).toBe("1");
    // The payload's own frozen markers are unchanged; the route adds the
    // marker that is derived at READ time.
    expect(body.nav_sessions_behind).toBe(0);
    expect(body.as_of).toBe("2026-03-13");
  });

  it("serves a fresh payload verbatim with no stale marker", async () => {
    vi.setSystemTime(new Date("2026-03-13T20:10:00Z"));
    mockDbSnapshots({ performance: PAYLOAD });
    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    const body = (await res.json()) as Record<string, unknown>;

    expect(body.stale).toBeUndefined();
    expect(res.headers.get("X-Radon-Stale")).toBeNull();
  });

  it("does not leave a dead background-rebuild trigger behind", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const here = resolve(fileURLToPath(import.meta.url), "..");
    const src = readFileSync(
      resolve(here, "..", "app", "api", "performance", "route.ts"),
      "utf8",
    );
    const code = src
      .split("\n")
      .filter((l) => !l.trimStart().startsWith("//") && !l.trimStart().startsWith("*"))
      .join("\n");
    expect(code).not.toContain("triggerBackgroundRebuild");
    expect(code).not.toContain("shouldRebuild");
  });
});
