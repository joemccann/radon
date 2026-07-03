/**
 * @vitest-environment node
 *
 * Stall diagnostics (2026-07-03). The destroy-storm fix contained the
 * ~hourly 3s read stalls, but their CAUSE is still undiagnosed. The watchdog
 * canary proves each stall is Node-local (Python read Turso fine at the same
 * minute), leaving three candidates: 8-conn queue saturation, stale-socket
 * reuse, or an event-loop stall. Two instruments discriminate:
 *
 *   1. dbExecute's failure warn includes a snapshot of the bounded Agent's
 *      per-origin stats (connected/free/pending/queued/running) — queued>0
 *      at timeout = queue saturation; idle pool = socket or loop.
 *   2. A loop-lag monitor logs when the event loop is blocked — a loop
 *      stall fires it in the same minute as the read timeouts.
 */
import { describe, it, expect, vi, afterEach } from "vitest";

const { createClientMock, agents, FakeAgent, undiciFetchMock } = vi.hoisted(() => {
  class FakeAgent {
    opts: Record<string, unknown>;
    stats = {
      "https://example.turso.io": { connected: 8, free: 0, pending: 3, queued: 5, running: 8, size: 16 },
    };
    destroy = vi.fn(async () => {});
    close = vi.fn(async () => {});
    constructor(opts: Record<string, unknown>) {
      this.opts = opts;
      agents.push(this);
    }
  }
  const agents: InstanceType<typeof FakeAgent>[] = [];
  return {
    createClientMock: vi.fn((config: Record<string, unknown>) => ({
      config,
      execute: vi.fn(async () => ({ rows: [] })),
      batch: vi.fn(),
    })),
    agents,
    FakeAgent,
    undiciFetchMock: vi.fn(async () => ({ ok: true, status: 200 })),
  };
});

vi.mock("@libsql/client", () => ({ createClient: createClientMock }));
vi.mock("undici", () => ({ Agent: FakeAgent, fetch: undiciFetchMock }));

async function freshDbModule() {
  vi.resetModules();
  agents.length = 0;
  process.env.TURSO_DB_URL = "libsql://example.turso.io";
  process.env.TURSO_AUTH_TOKEN = "token";
  return import("../lib/db");
}

afterEach(() => {
  vi.restoreAllMocks();
  delete process.env.TURSO_DB_URL;
  delete process.env.TURSO_AUTH_TOKEN;
});

describe("getPoolStats", () => {
  it("returns null before any pooled request", async () => {
    const db = await freshDbModule();
    expect(db.getPoolStats()).toBeNull();
  });

  it("returns the bounded Agent's per-origin stats once the pool exists", async () => {
    const db = await freshDbModule();
    db.getDb();
    const boundedFetch = createClientMock.mock.calls.at(-1)![0].fetch as typeof db.pooledDbFetch;
    await boundedFetch("https://example.turso.io/v2/pipeline");
    expect(db.getPoolStats()).toEqual({
      "https://example.turso.io": { connected: 8, free: 0, pending: 3, queued: 5, running: 8, size: 16 },
    });
  });
});

describe("dbExecute failure warn carries pool stats", () => {
  it("includes the stats snapshot so a saturation-at-timeout is visible in journald", async () => {
    vi.resetModules();
    vi.doMock("@/lib/db", () => ({
      getDb: () => ({ execute: vi.fn(async () => { throw new Error("db read timed out after 3000ms"); }) }),
      resetDb: vi.fn(),
      getPoolStats: () => ({ "https://x": { queued: 5, running: 8 } }),
    }));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { dbExecute } = await import("../lib/dbExecute");
    await expect(dbExecute("SELECT 1", { label: "diag" })).rejects.toThrow();
    const line = warn.mock.calls.map((c) => c.join(" ")).find((l) => l.includes("[dbExecute:diag]"));
    expect(line).toBeTruthy();
    expect(line).toContain('"queued":5');
  });

  it("stays safe when the db module mock lacks getPoolStats (wholesale route-test mocks)", async () => {
    vi.resetModules();
    vi.doMock("@/lib/db", () => ({
      getDb: () => ({ execute: vi.fn(async () => { throw new Error("boom"); }) }),
      resetDb: vi.fn(),
    }));
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const { dbExecute } = await import("../lib/dbExecute");
    await expect(dbExecute("SELECT 1")).rejects.toThrow("boom");
  });
});

describe("loop-lag monitor", () => {
  it("warns when a tick arrives later than the threshold", async () => {
    vi.resetModules();
    vi.useFakeTimers();
    const warn = vi.fn();
    // now() runs 1500ms late on the second tick relative to the timer clock.
    let fakeNow = 0;
    vi.setSystemTime(0);
    const { startLoopLagMonitor } = await import("../lib/loopLagMonitor");
    const stop = startLoopLagMonitor({ intervalMs: 500, thresholdMs: 1000, now: () => fakeNow, warn });

    fakeNow = 500; // on time
    vi.advanceTimersByTime(500);
    expect(warn).not.toHaveBeenCalled();

    fakeNow = 2500; // tick delivered 1500ms late (loop was blocked)
    vi.advanceTimersByTime(500);
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0][0])).toMatch(/loop-lag.*1500ms/);

    stop();
    vi.useRealTimers();
  });

  it("returns a stop function that halts ticking", async () => {
    vi.resetModules();
    vi.useFakeTimers();
    const warn = vi.fn();
    let fakeNow = 0;
    const { startLoopLagMonitor } = await import("../lib/loopLagMonitor");
    const stop = startLoopLagMonitor({ intervalMs: 500, thresholdMs: 100, now: () => fakeNow, warn });
    stop();
    fakeNow = 60_000;
    vi.advanceTimersByTime(5_000);
    expect(warn).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
