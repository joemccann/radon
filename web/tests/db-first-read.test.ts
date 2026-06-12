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

let warn: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  warn = vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  warn.mockRestore();
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
