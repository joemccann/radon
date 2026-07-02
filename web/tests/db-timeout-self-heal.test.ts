/**
 * @vitest-environment node
 *
 * Every DB read timeout must drop the cached libsql client (resetDb) so a
 * wedged undici pool never survives past one failed request — the
 * generalisation of the service-health route's self-heal (2026-07-02
 * incident: 523 timeout bursts since Jun 26, each cleared only by a process
 * restart).
 *
 * Pins the reset on the three read paths:
 *   - dbExecute (the shared execute-with-timeout-and-reset chokepoint)
 *   - dbFirstRead (DB source timeout → reset; disk failure → NO reset)
 *   - readOrdersFromDb (routes through dbExecute)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const hanging = () => new Promise<never>(() => {});

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function mockDb(overrides: {
  execute?: (...args: unknown[]) => Promise<unknown>;
  syncDb?: () => Promise<void>;
}) {
  const resetDb = vi.fn();
  vi.doMock("@/lib/db", () => ({
    getDb: () => ({ execute: overrides.execute ?? hanging }),
    syncDb: overrides.syncDb ?? (async () => {}),
    resetDb,
  }));
  return { resetDb };
}

describe("dbExecute self-heal", () => {
  it("calls resetDb when the read times out", async () => {
    const { resetDb } = mockDb({ execute: hanging });
    const { dbExecute } = await import("../lib/dbExecute");
    await expect(
      dbExecute({ sql: "SELECT 1", args: [] }, { timeoutMs: 20, label: "test" }),
    ).rejects.toThrow(/timed out/);
    expect(resetDb).toHaveBeenCalledTimes(1);
  });

  it("calls resetDb when the read rejects outright", async () => {
    const { resetDb } = mockDb({
      execute: () => Promise.reject(new Error("socket hang up")),
    });
    const { dbExecute } = await import("../lib/dbExecute");
    await expect(
      dbExecute({ sql: "SELECT 1", args: [] }, { timeoutMs: 20 }),
    ).rejects.toThrow("socket hang up");
    expect(resetDb).toHaveBeenCalledTimes(1);
  });

  it("does NOT reset on success", async () => {
    const { resetDb } = mockDb({ execute: async () => ({ rows: [] }) });
    const { dbExecute } = await import("../lib/dbExecute");
    await dbExecute({ sql: "SELECT 1", args: [] });
    expect(resetDb).not.toHaveBeenCalled();
  });
});

describe("dbFirstRead self-heal", () => {
  let warn: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  });
  afterEach(() => {
    warn.mockRestore();
  });

  it("calls resetDb when the DB source times out (disk still serves)", async () => {
    const { resetDb } = mockDb({});
    const { dbFirstRead } = await import("../lib/dbFirstRead");
    const result = await dbFirstRead({
      fromDb: () => hanging(),
      fromDisk: async () => ({ data: { ok: 1 }, timestampMs: Date.now() }),
      maxAgeMs: 60_000,
      sourceTimeoutMs: 20,
    });
    expect(result).toMatchObject({ ok: true, source: "disk" });
    expect(resetDb).toHaveBeenCalledTimes(1);
  });

  it("does NOT reset when only the disk source fails", async () => {
    const { resetDb } = mockDb({});
    const { dbFirstRead } = await import("../lib/dbFirstRead");
    const result = await dbFirstRead({
      fromDb: async () => ({ data: { ok: 1 }, timestampMs: Date.now() }),
      fromDisk: () => Promise.reject(new Error("ENOENT")),
      maxAgeMs: 60_000,
      sourceTimeoutMs: 20,
    });
    expect(result).toMatchObject({ ok: true, source: "db" });
    expect(resetDb).not.toHaveBeenCalled();
  });
});

describe("readOrdersFromDb self-heal", () => {
  it("calls resetDb when the open-orders read times out", async () => {
    vi.useFakeTimers();
    const { resetDb } = mockDb({ execute: hanging });
    const { readOrdersFromDb } = await import("../lib/orders/readOrdersFromDb");

    const read = readOrdersFromDb();
    const assertion = expect(read).rejects.toThrow(/timed out/);
    await vi.advanceTimersByTimeAsync(3_100);
    await assertion;
    expect(resetDb).toHaveBeenCalled();
  });

  it("calls resetDb when the replica sync times out (best-effort read continues)", async () => {
    vi.useFakeTimers();
    const { resetDb } = mockDb({
      execute: async () => ({ rows: [] }),
      syncDb: () => hanging(),
    });
    const { readOrdersFromDb } = await import("../lib/orders/readOrdersFromDb");

    const read = readOrdersFromDb();
    await vi.advanceTimersByTimeAsync(3_100);
    await expect(read).resolves.toBeNull();
    expect(resetDb).toHaveBeenCalled();
  });
});
