/**
 * Turso-first chokepoint with a last-in-memory fallback (DUR-01).
 *
 * A valid, non-degraded DB row is served and remembered. When Turso fails, times
 * out, returns nothing, returns a future-dated row or returns a degraded row, the
 * last good read for that label serves instead. Disk is never a source.
 *
 * `fresh` always comes from the served read's OWN timestamp, so memory can keep a
 * panel rendering through an outage but can never make dead data look current.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { runWithDemoDbPrincipal } from "../lib/demo/demoDbIsolation";
import {
  dbFirstRead,
  contentTimestampMs,
  isMissingPayload,
  staleCollapse,
  __resetDbFirstReadMemoryForTests,
  type TimestampedRead,
} from "../lib/dbFirstRead";

const NOW = Date.parse("2026-06-12T15:00:00Z");
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

function source<T>(data: T, timestampMs: number | null) {
  return async (): Promise<TimestampedRead<T>> => ({ data, timestampMs });
}

const absent = async () => null;
const failing = async () => {
  throw new Error("WAL locked");
};

const baseOptions = { maxAgeMs: 10 * MINUTE, now: () => NOW, label: "test" };

/** Fixture predicate: a route whose degraded shape is an empty `results` array. */
const isEmptyResults = (data: { results: unknown[] }): boolean => data.results.length === 0;

let warn: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  __resetDbFirstReadMemoryForTests();
  warn = vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  warn.mockRestore();
  vi.useRealTimers();
  vi.unstubAllEnvs();
});

function warned(...fragments: string[]): boolean {
  return warn.mock.calls.some((call: unknown[]) =>
    fragments.every((fragment) => String(call[0]).includes(fragment)),
  );
}

describe("dbFirstRead — DB selection", () => {
  it("serves a valid DB row with source db", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "db" }, NOW - 1 * MINUTE),
    });
    expect(result).toEqual({
      ok: true,
      source: "db",
      data: { v: "db" },
      timestampMs: NOW - 1 * MINUTE,
      fresh: true,
    });
    expect(warn).not.toHaveBeenCalled();
  });

  it("an older healthy DB row replaces a newer memory entry: Turso is the source of truth", async () => {
    await dbFirstRead({ ...baseOptions, fromDb: source({ v: "newer" }, NOW - 1 * MINUTE) });
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "older" }, NOW - 5 * MINUTE),
    });
    expect(result.ok && result.source).toBe("db");
    expect(result.ok && result.data).toEqual({ v: "older" });
  });

  it("an untimestamped row is served and reads stale", async () => {
    const result = await dbFirstRead({ ...baseOptions, fromDb: source({ v: "db" }, null) });
    expect(result).toEqual({
      ok: true,
      source: "db",
      data: { v: "db" },
      timestampMs: null,
      fresh: false,
    });
  });
});

describe("dbFirstRead — last-in-memory fallback", () => {
  async function remember(data: unknown = { v: "remembered" }, ageMs = 1 * MINUTE): Promise<void> {
    await dbFirstRead({ ...baseOptions, fromDb: source(data, NOW - ageMs) });
  }

  it("serves memory when the DB read throws, with a labelled warning", async () => {
    await remember();
    const result = await dbFirstRead({ ...baseOptions, fromDb: failing, label: "test" });
    expect(result).toEqual({
      ok: true,
      source: "memory",
      data: { v: "remembered" },
      timestampMs: NOW - 1 * MINUTE,
      fresh: true,
    });
    expect(warned("test", "WAL locked")).toBe(true);
    expect(warned("test", "memory")).toBe(true);
  });

  it("serves memory when the DB returns no row", async () => {
    await remember();
    const result = await dbFirstRead({ ...baseOptions, fromDb: absent });
    expect(result.ok && result.source).toBe("memory");
    expect(result.ok && result.data).toEqual({ v: "remembered" });
  });

  it("serves memory when the DB row is future-dated beyond the skew allowance", async () => {
    await remember();
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "future" }, NOW + 5 * MINUTE),
    });
    expect(result.ok && result.source).toBe("memory");
    expect(result.ok && result.data).toEqual({ v: "remembered" });
  });

  it("reports fresh=false for a memory entry past maxAgeMs, so stale gating still fires", async () => {
    await remember({ v: "old" }, 2 * HOUR);
    const result = await dbFirstRead({ ...baseOptions, fromDb: failing });
    expect(result).toEqual({
      ok: true,
      source: "memory",
      data: { v: "old" },
      timestampMs: NOW - 2 * HOUR,
      fresh: false,
    });
  });

  it("memory ages on the serving clock, never freezes at the time it was stored", async () => {
    await remember({ v: "aging" }, 1 * MINUTE);
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: failing,
      now: () => NOW + 30 * MINUTE,
    });
    expect(result.ok && result.source).toBe("memory");
    expect(result.ok && result.timestampMs).toBe(NOW - 1 * MINUTE);
    expect(result.ok && result.fresh).toBe(false);
  });

  it("a memory entry with no timestamp stays stale", async () => {
    await dbFirstRead({ ...baseOptions, fromDb: source({ v: "undated" }, null) });
    const result = await dbFirstRead({ ...baseOptions, fromDb: failing });
    expect(result.ok && result.source).toBe("memory");
    expect(result.ok && result.fresh).toBe(false);
  });

  it("staleCollapse on a stale memory read collapses to missing and carries the row's own scan_time", async () => {
    await remember({ scan_time: "2026-06-12T13:00:00Z", v: "old" }, 2 * HOUR);
    const result = await dbFirstRead({ ...baseOptions, fromDb: failing });
    expect(result.ok && result.fresh).toBe(false);
    expect(staleCollapse({ missing: true, scan_time: null }, result)).toEqual({
      missing: true,
      stale: true,
      scan_time: "2026-06-12T13:00:00Z",
    });
  });

  it("returns ok=false with no row and no memory", async () => {
    await expect(dbFirstRead({ ...baseOptions, fromDb: absent })).resolves.toEqual({ ok: false });
  });

  it("returns ok=false when the DB throws and memory is empty", async () => {
    await expect(dbFirstRead({ ...baseOptions, fromDb: failing })).resolves.toEqual({ ok: false });
  });

  it("returns ok=false when the only read is future-dated: it must not be remembered either", async () => {
    await expect(dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "future" }, NOW + 5 * MINUTE),
    })).resolves.toEqual({ ok: false });
    await expect(dbFirstRead({ ...baseOptions, fromDb: failing })).resolves.toEqual({ ok: false });
  });
});

describe("dbFirstRead — labels do not bleed", () => {
  it("memory is keyed by label", async () => {
    await dbFirstRead({
      ...baseOptions,
      label: "vixts",
      fromDb: source({ v: "vixts" }, NOW - 1 * MINUTE),
    });
    await expect(dbFirstRead({
      ...baseOptions,
      label: "skew",
      fromDb: failing,
    })).resolves.toEqual({ ok: false });
    const vixts = await dbFirstRead({ ...baseOptions, label: "vixts", fromDb: failing });
    expect(vixts.ok && vixts.data).toEqual({ v: "vixts" });
  });

  it("evicts the least recently stored label past the bound, and keeps labels it keeps seeing", async () => {
    await dbFirstRead({
      ...baseOptions,
      label: "survivor",
      fromDb: source({ v: "survivor" }, NOW - 1 * MINUTE),
    });
    await dbFirstRead({
      ...baseOptions,
      label: "victim",
      fromDb: source({ v: "victim" }, NOW - 1 * MINUTE),
    });
    await dbFirstRead({
      ...baseOptions,
      label: "survivor",
      fromDb: source({ v: "survivor" }, NOW - 1 * MINUTE),
    });
    for (let i = 0; i < 256; i += 1) {
      await dbFirstRead({
        ...baseOptions,
        label: `ticker:${i}`,
        fromDb: source({ i }, NOW - 1 * MINUTE),
      });
    }
    await expect(dbFirstRead({
      ...baseOptions,
      label: "victim",
      fromDb: failing,
    })).resolves.toEqual({ ok: false });
    await expect(dbFirstRead({
      ...baseOptions,
      label: "ticker:255",
      fromDb: failing,
    })).resolves.toMatchObject({ ok: true, source: "memory" });
  });
});

describe("dbFirstRead — tenant isolation", () => {
  it("a demo-scoped caller never receives memory stored from an operator read", async () => {
    await dbFirstRead({
      ...baseOptions,
      label: "performance",
      fromDb: source({ operator_nav: 1_000_000 }, NOW - 1 * MINUTE),
    });
    // In the prod process getDb() throws DemoDbIsolationError for this scope, so
    // the DB-failed path is exactly how a shared key would have leaked the row.
    const demo = await runWithDemoDbPrincipal(() => dbFirstRead({
      ...baseOptions,
      label: "performance",
      fromDb: failing,
    }));
    expect(demo).toEqual({ ok: false });
  });

  it("an operator caller never receives memory stored from a demo-scoped read", async () => {
    await runWithDemoDbPrincipal(() => dbFirstRead({
      ...baseOptions,
      label: "vixts",
      fromDb: source({ v: "demo-mirror" }, NOW - 1 * MINUTE),
    }));
    await expect(dbFirstRead({
      ...baseOptions,
      label: "vixts",
      fromDb: failing,
    })).resolves.toEqual({ ok: false });
  });

  it("memory is keyed by database, so a changed TURSO_DB_URL starts empty", async () => {
    vi.stubEnv("TURSO_DB_URL", "libsql://radon-one.example");
    await dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "one" }, NOW - 1 * MINUTE),
    });
    vi.stubEnv("TURSO_DB_URL", "libsql://radon-two.example");
    await expect(dbFirstRead({ ...baseOptions, fromDb: failing })).resolves.toEqual({ ok: false });
    vi.stubEnv("TURSO_DB_URL", "libsql://radon-one.example");
    const one = await dbFirstRead({ ...baseOptions, fromDb: failing });
    expect(one.ok && one.data).toEqual({ v: "one" });
  });
});

describe("dbFirstRead — source deadline", () => {
  it("serves memory when the DB read hangs past the source timeout", async () => {
    await dbFirstRead({
      ...baseOptions,
      label: "live-data",
      fromDb: source({ v: "remembered" }, NOW - 1 * MINUTE),
    });
    vi.useFakeTimers();
    const resultPromise = dbFirstRead({
      ...baseOptions,
      fromDb: () => new Promise(() => {}),
      sourceTimeoutMs: 10,
      label: "live-data",
    });

    await vi.advanceTimersByTimeAsync(10);
    const result = await resultPromise;

    expect(result).toEqual({
      ok: true,
      source: "memory",
      data: { v: "remembered" },
      timestampMs: NOW - 1 * MINUTE,
      fresh: true,
    });
    expect(warned("live-data", "DB read timed out")).toBe(true);
  });

  it("returns ok=false when the DB read hangs and memory is empty", async () => {
    vi.useFakeTimers();
    const resultPromise = dbFirstRead({
      ...baseOptions,
      fromDb: () => new Promise(() => {}),
      sourceTimeoutMs: 10,
      label: "live-data",
    });

    await vi.advanceTimersByTimeAsync(10);
    await expect(resultPromise).resolves.toEqual({ ok: false });
  });
});

describe("dbFirstRead — isDegraded gate (T-042)", () => {
  it("a degraded DB row loses to healthy memory, even though it is fresher", async () => {
    await dbFirstRead({
      ...baseOptions,
      isDegraded: isEmptyResults,
      fromDb: source({ results: [1, 2, 3] }, NOW - 20 * MINUTE),
    });
    const result = await dbFirstRead({
      ...baseOptions,
      isDegraded: isEmptyResults,
      fromDb: source({ results: [] as number[] }, NOW - 1 * MINUTE),
    });
    expect(result).toEqual({
      ok: true,
      source: "memory",
      data: { results: [1, 2, 3] },
      timestampMs: NOW - 20 * MINUTE,
      fresh: false,
    });
    expect(warned("test", "memory")).toBe(true);
  });

  it("missing:true loses to healthy memory", async () => {
    await dbFirstRead({
      ...baseOptions,
      isDegraded: isMissingPayload,
      fromDb: source({ v: "stale-but-real" }, NOW - 30 * MINUTE),
    });
    const result = await dbFirstRead({
      ...baseOptions,
      isDegraded: isMissingPayload,
      fromDb: source({ v: "x", missing: true }, NOW - 1 * MINUTE),
    });
    expect(result.ok && result.source).toBe("memory");
    expect(result.ok && result.data).toEqual({ v: "stale-but-real" });
  });

  it("a degraded row with no memory is still served, from the DB, with its own freshness", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      isDegraded: isEmptyResults,
      fromDb: source({ results: [] as number[] }, NOW - 1 * MINUTE),
    });
    expect(result).toEqual({
      ok: true,
      source: "db",
      data: { results: [] },
      timestampMs: NOW - 1 * MINUTE,
      fresh: true,
    });
  });

  it("a degraded row is never remembered", async () => {
    await dbFirstRead({
      ...baseOptions,
      isDegraded: isEmptyResults,
      fromDb: source({ results: [] as number[] }, NOW - 1 * MINUTE),
    });
    await expect(dbFirstRead({
      ...baseOptions,
      isDegraded: isEmptyResults,
      fromDb: failing,
    })).resolves.toEqual({ ok: false });
  });

  it("without isDegraded (opt-out routes), an empty row is a legitimate read and replaces memory", async () => {
    await dbFirstRead({ ...baseOptions, fromDb: source({ results: [1, 2, 3] }, NOW - 20 * MINUTE) });
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ results: [] as number[] }, NOW - 1 * MINUTE),
    });
    expect(result.ok && result.source).toBe("db");
    expect(result.ok && result.data).toEqual({ results: [] });
    const fallback = await dbFirstRead({ ...baseOptions, fromDb: failing });
    expect(fallback.ok && fallback.data).toEqual({ results: [] });
  });
});

describe("contentTimestampMs", () => {
  it("parses naive ISO strings as UTC (Hetzner Python writers)", () => {
    expect(contentTimestampMs("2026-06-12T15:00:00")).toBe(NOW);
  });

  it("parses timezone-aware strings unchanged", () => {
    expect(contentTimestampMs("2026-06-12T15:00:00+00:00")).toBe(NOW);
  });

  it("returns null for missing, empty, or unparseable values", () => {
    expect(contentTimestampMs(undefined)).toBeNull();
    expect(contentTimestampMs(null)).toBeNull();
    expect(contentTimestampMs("")).toBeNull();
    expect(contentTimestampMs("not-a-date")).toBeNull();
    expect(contentTimestampMs(12345)).toBeNull();
  });
});

describe("isMissingPayload", () => {
  it("treats null/undefined payloads as missing", () => {
    expect(isMissingPayload(null)).toBe(true);
    expect(isMissingPayload(undefined)).toBe(true);
  });

  it("treats an explicit missing:true marker as missing", () => {
    expect(isMissingPayload({ missing: true, v: "x" })).toBe(true);
  });

  it("treats a populated object without missing:true as present", () => {
    expect(isMissingPayload({ v: "x" })).toBe(false);
    expect(isMissingPayload({ missing: false, v: "x" })).toBe(false);
    expect(isMissingPayload({})).toBe(false);
  });

  it("treats non-object primitives as present (never absent by construction)", () => {
    expect(isMissingPayload("x")).toBe(false);
    expect(isMissingPayload(0)).toBe(false);
    expect(isMissingPayload(false)).toBe(false);
  });
});
