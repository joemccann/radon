/**
 * Freshness-aware DB-vs-disk chokepoint (DUR-01).
 *
 * The helper must serve whichever source carries the NEWER content
 * timestamp so a stalled writer on either side (frozen Turso mirror,
 * stale disk fallback) can never freeze the UI. Missing/unparseable
 * timestamps are infinitely stale; the DB wins ties.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  dbFirstRead,
  contentTimestampMs,
  isMissingPayload,
  type TimestampedRead,
} from "../lib/dbFirstRead";

const NOW = Date.parse("2026-06-12T15:00:00Z");
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

function source<T>(data: T, timestampMs: number | null) {
  return async (): Promise<TimestampedRead<T>> => ({ data, timestampMs });
}

const absent = async () => null;

const baseOptions = { maxAgeMs: 10 * MINUTE, now: () => NOW };

/** T-042 fixture predicate: a route whose degraded shape is an empty `results` array. */
const isEmptyResults = (data: { results: unknown[] }): boolean => data.results.length === 0;

let warn: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  warn = vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  warn.mockRestore();
  vi.useRealTimers();
});

describe("dbFirstRead — fresher-source selection", () => {
  it("serves the DB snapshot when it is fresher than disk", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "db" }, NOW - 1 * MINUTE),
      fromDisk: source({ v: "disk" }, NOW - 5 * MINUTE),
    });
    expect(result).toEqual({
      ok: true,
      source: "db",
      data: { v: "db" },
      timestampMs: NOW - 1 * MINUTE,
      fresh: true,
    });
  });

  it("serves the disk snapshot when it is fresher than the DB (frozen DB mirror / laptop-thin mode)", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "db" }, NOW - 24 * HOUR),
      fromDisk: source({ v: "disk" }, NOW - 1 * MINUTE),
      label: "vcg",
    });
    expect(result).toEqual({
      ok: true,
      source: "disk",
      data: { v: "disk" },
      timestampMs: NOW - 1 * MINUTE,
      fresh: true,
    });
    // Drift signal: a present-but-staler DB row must be flagged in logs.
    expect(warn.mock.calls.some((c: unknown[]) => String(c[0]).includes("vcg"))).toBe(true);
  });

  it("DB wins timestamp ties", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "db" }, NOW - 2 * MINUTE),
      fromDisk: source({ v: "disk" }, NOW - 2 * MINUTE),
    });
    expect(result.ok && result.source).toBe("db");
  });
});

describe("dbFirstRead — missing timestamps are infinitely stale", () => {
  it("a timestamped disk snapshot beats an untimestamped DB snapshot", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "db" }, null),
      fromDisk: source({ v: "disk" }, NOW - 9 * HOUR),
    });
    expect(result.ok && result.source).toBe("disk");
  });

  it("a timestamped DB snapshot beats an untimestamped disk snapshot", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "db" }, NOW - 9 * HOUR),
      fromDisk: source({ v: "disk" }, null),
    });
    expect(result.ok && result.source).toBe("db");
  });

  it("both untimestamped → legacy DB-first order, fresh=false", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "db" }, null),
      fromDisk: source({ v: "disk" }, null),
    });
    expect(result).toEqual({
      ok: true,
      source: "db",
      data: { v: "db" },
      timestampMs: null,
      fresh: false,
    });
  });
});

describe("dbFirstRead — per-route max-age", () => {
  it("reports fresh=true when the served snapshot is within maxAgeMs", async () => {
    const result = await dbFirstRead({
      fromDb: source({ v: "db" }, NOW - 5 * MINUTE),
      fromDisk: absent,
      maxAgeMs: 10 * MINUTE,
      now: () => NOW,
    });
    expect(result.ok && result.fresh).toBe(true);
  });

  it("reports fresh=false when the served snapshot exceeds maxAgeMs", async () => {
    const result = await dbFirstRead({
      fromDb: source({ v: "db" }, NOW - 5 * MINUTE),
      fromDisk: absent,
      maxAgeMs: 2 * MINUTE,
      now: () => NOW,
    });
    expect(result.ok && result.fresh).toBe(false);
  });

  it("treats age exactly equal to maxAgeMs as fresh", async () => {
    const result = await dbFirstRead({
      fromDb: source({ v: "db" }, NOW - 2 * MINUTE),
      fromDisk: absent,
      maxAgeMs: 2 * MINUTE,
      now: () => NOW,
    });
    expect(result.ok && result.fresh).toBe(true);
  });
});

describe("dbFirstRead — failure handling", () => {
  it("falls back to disk when fromDb throws", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: async () => {
        throw new Error("WAL locked");
      },
      fromDisk: source({ v: "disk" }, NOW - 1 * MINUTE),
    });
    expect(result.ok && result.source).toBe("disk");
    expect(warn.mock.calls.some((c: unknown[]) => String(c[0]).includes("WAL locked"))).toBe(true);
  });

  it("serves DB when fromDisk throws", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "db" }, NOW - 1 * MINUTE),
      fromDisk: async () => {
        throw new Error("ENOENT");
      },
    });
    expect(result.ok && result.source).toBe("db");
  });

  it("warns with the disk-fallback message when DB is empty", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: absent,
      fromDisk: source({ v: "disk" }, NOW - 1 * MINUTE),
      label: "scanner",
    });
    expect(result.ok && result.source).toBe("disk");
    expect(warn.mock.calls.some((c: unknown[]) => String(c[0]).includes("scanner"))).toBe(true);
  });

  it("returns ok=false when both sources are absent", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: absent,
      fromDisk: absent,
    });
    expect(result).toEqual({ ok: false });
  });

  it("returns ok=false when both sources throw", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: async () => {
        throw new Error("db");
      },
      fromDisk: async () => {
        throw new Error("disk");
      },
    });
    expect(result).toEqual({ ok: false });
  });
});

describe("dbFirstRead — source deadlines", () => {
  it("serves disk when the DB read hangs past the source timeout", async () => {
    vi.useFakeTimers();
    const resultPromise = dbFirstRead({
      ...baseOptions,
      fromDb: () => new Promise(() => {}),
      fromDisk: source({ v: "disk" }, NOW - 1 * MINUTE),
      sourceTimeoutMs: 10,
      label: "live-data",
    });

    await vi.advanceTimersByTimeAsync(10);
    const result = await resultPromise;

    expect(result).toEqual({
      ok: true,
      source: "disk",
      data: { v: "disk" },
      timestampMs: NOW - 1 * MINUTE,
      fresh: true,
    });
    expect(
      warn.mock.calls.some((c: unknown[]) =>
        String(c[0]).includes("live-data") &&
        String(c[0]).includes("DB read timed out"),
      ),
    ).toBe(true);
  });

  it("returns ok=false when both live-data sources hang past their source timeout", async () => {
    vi.useFakeTimers();
    const resultPromise = dbFirstRead({
      ...baseOptions,
      fromDb: () => new Promise(() => {}),
      fromDisk: () => new Promise(() => {}),
      sourceTimeoutMs: 10,
      label: "live-data",
    });

    await vi.advanceTimersByTimeAsync(10);
    await expect(resultPromise).resolves.toEqual({ ok: false });
  });
});

describe("dbFirstRead — isDegraded gate (T-042)", () => {
  it("a fresher structurally-empty {results: []} source loses to a populated older source", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ results: [1, 2, 3] }, NOW - 20 * MINUTE),
      fromDisk: source({ results: [] as number[] }, NOW - 1 * MINUTE),
      isDegraded: isEmptyResults,
    });
    expect(result).toEqual({
      ok: true,
      source: "db",
      data: { results: [1, 2, 3] },
      timestampMs: NOW - 20 * MINUTE,
      fresh: false,
    });
  });

  it("(and vice versa) a fresher structurally-empty DB source loses to a populated older disk source", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ results: [] as number[] }, NOW - 1 * MINUTE),
      fromDisk: source({ results: [1] }, NOW - 20 * MINUTE),
      isDegraded: isEmptyResults,
    });
    expect(result.ok && result.source).toBe("disk");
    expect(result.ok && result.data).toEqual({ results: [1] });
  });

  it("missing:true loses to a populated older source regardless of freshness", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ v: "stale-but-real" }, NOW - 30 * MINUTE),
      fromDisk: source({ v: "x", missing: true }, NOW - 1 * MINUTE),
      isDegraded: isMissingPayload,
    });
    expect(result.ok && result.source).toBe("db");
    expect(result.ok && result.data).toEqual({ v: "stale-but-real" });
  });

  it("both sources degraded → keeps the ordinary freshness order (fresher of the two still wins)", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ results: [] as number[] }, NOW - 20 * MINUTE),
      fromDisk: source({ results: [] as number[] }, NOW - 1 * MINUTE),
      isDegraded: isEmptyResults,
    });
    expect(result.ok && result.source).toBe("disk");
  });

  it("both sources degraded and tied on timestamp → DB still wins ties", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ results: [] as number[] }, NOW - 5 * MINUTE),
      fromDisk: source({ results: [] as number[] }, NOW - 5 * MINUTE),
      isDegraded: isEmptyResults,
    });
    expect(result.ok && result.source).toBe("db");
  });

  it("neither source degraded → behaves exactly like the freshness-only path", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ results: [1] }, NOW - 1 * MINUTE),
      fromDisk: source({ results: [2] }, NOW - 5 * MINUTE),
      isDegraded: isEmptyResults,
    });
    expect(result.ok && result.source).toBe("db");
  });

  it("without isDegraded (opt-out routes), a fresher empty payload still wins on timestamp alone — unchanged legacy behavior", async () => {
    const result = await dbFirstRead({
      ...baseOptions,
      fromDb: source({ results: [1, 2, 3] }, NOW - 20 * MINUTE),
      fromDisk: source({ results: [] as number[] }, NOW - 1 * MINUTE),
    });
    expect(result.ok && result.source).toBe("disk");
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
