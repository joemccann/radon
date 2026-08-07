/**
 * syncDb must be a true no-op in HTTP mode (2026-08-06 root cause).
 *
 * The libsql embedded replica was retired 2026-05-20 (direct-to-cloud,
 * DUR-07). `syncDb()` feature-detects with `"sync" in db`, but the HTTP
 * client DEFINES sync() and throws
 * `LibsqlError("sync not supported in http mode", "SYNC_NOT_SUPPORTED")`.
 * So the guard passes, the call throws, and readOrdersFromDb's catch
 * calls resetDb — arming a pool teardown on EVERY /orders read. Two arms
 * inside the 10s cluster window escalate to a real Agent.destroy(), which
 * aborts every in-flight request (UND_ERR_DESTROYED) and 503s unrelated
 * routes: watchlist, profile, portfolio.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const syncCalls = { count: 0 };

vi.mock("@libsql/client", () => {
  class FakeLibsqlError extends Error {
    code: string;
    constructor(message: string, code: string) {
      super(message);
      this.code = code;
    }
  }
  return {
    createClient: () => ({
      execute: async () => ({ rows: [] }),
      batch: async () => [],
      // Mirrors @libsql/client's http.js: defined, throws synchronously.
      sync: () => {
        syncCalls.count += 1;
        throw new FakeLibsqlError("sync not supported in http mode", "SYNC_NOT_SUPPORTED");
      },
    }),
  };
});

describe("syncDb in HTTP mode", () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(async () => {
    process.env.TURSO_DB_URL = "libsql://sync-test.turso.io";
    process.env.TURSO_AUTH_TOKEN = "test-token";
    delete process.env.RADON_DB_USE_REPLICA;
    syncCalls.count = 0;
    const { __resetDbForTests } = await import("@/lib/db");
    __resetDbForTests();
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(async () => {
    warnSpy.mockRestore();
    const { __resetDbForTests } = await import("@/lib/db");
    __resetDbForTests();
  });

  it("does not throw when the client cannot sync", async () => {
    const { syncDb } = await import("@/lib/db");
    await expect(syncDb()).resolves.toBeUndefined();
  });

  it("never invokes sync() without an enabled replica", async () => {
    const { syncDb } = await import("@/lib/db");
    await syncDb();
    expect(syncCalls.count).toBe(0);
  });

  it("arms no pool teardown — the /orders 503 trigger", async () => {
    const { syncDb } = await import("@/lib/db");
    await syncDb();
    const teardown = warnSpy.mock.calls
      .map((call) => call.map(String).join(" "))
      .filter((line) => line.includes("[db] pool"));
    expect(teardown).toEqual([]);
  });
});
