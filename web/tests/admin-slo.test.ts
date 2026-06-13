/**
 * DUR-16: 7-day SLO attainment tiles for /admin, computed from the
 * append-only external_probe_runs history (migration 0013).
 *
 * SLO definitions (fixed by the DUR-16 contract):
 *   edge reachability   99.5% / 7d   <- edge_ok
 *   RTH tick freshness  99%   / 7d   <- tick_fresh
 *   scan freshness      95%   / 7d   <- scan_fresh
 *
 * NULL columns mean "not applicable on that run" (quiet market, endpoint
 * pending) and are EXCLUDED from the denominator — never counted as misses.
 * An empty/missing table renders "--" (attainment null), never a fabricated
 * 100%.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGetDb = vi.fn();
vi.mock("@/lib/db", () => ({ getDb: mockGetDb }));

import {
  SLO_DEFINITIONS,
  SLO_WINDOW_MS,
  summarizeSlos,
  type ExternalProbeRunRow,
  type SloPayload,
} from "../lib/adminSlo";

function run(overrides: Partial<ExternalProbeRunRow> = {}): ExternalProbeRunRow {
  return {
    run_at: "2026-06-10T15:00:00Z",
    edge_ok: 1,
    user_path_ok: 1,
    freshness_ok: 1,
    tick_fresh: 1,
    scan_fresh: 1,
    latency_ms: 250,
    ...overrides,
  };
}

describe("SLO definitions — pinned targets", () => {
  it("matches the DUR-16 contract", () => {
    expect(SLO_DEFINITIONS.map((d) => [d.key, d.field, d.targetPct])).toEqual([
      ["edge", "edge_ok", 99.5],
      ["tick", "tick_fresh", 99],
      ["scan", "scan_fresh", 95],
    ]);
    expect(SLO_WINDOW_MS).toBe(7 * 24 * 3_600_000);
  });
});

describe("summarizeSlos", () => {
  it("computes attainment per SLO from synthetic rows", () => {
    const rows = [
      run(),
      run(),
      run({ edge_ok: 0 }),
      run({ tick_fresh: 0 }),
    ];
    const [edge, tick, scan] = summarizeSlos(rows);

    expect(edge.attainmentPct).toBeCloseTo(75);
    expect(edge.samples).toBe(4);
    expect(edge.met).toBe(false);

    expect(tick.attainmentPct).toBeCloseTo(75);
    expect(tick.met).toBe(false);

    expect(scan.attainmentPct).toBe(100);
    expect(scan.samples).toBe(4);
    expect(scan.met).toBe(true);
  });

  it("excludes NULL samples from the denominator (quiet market is not a miss)", () => {
    const rows = [
      run({ tick_fresh: null, scan_fresh: null }), // off-hours run
      run({ tick_fresh: null, scan_fresh: null }),
      run(),
      run({ tick_fresh: 0 }),
    ];
    const [edge, tick, scan] = summarizeSlos(rows);

    expect(edge.samples).toBe(4);
    expect(tick.samples).toBe(2);
    expect(tick.attainmentPct).toBeCloseTo(50);
    expect(scan.samples).toBe(2);
    expect(scan.attainmentPct).toBe(100);
  });

  it("attainment exactly at target counts as met", () => {
    // 199/200 = 99.5% — exactly the edge target.
    const rows = [...Array(199).fill(null).map(() => run()), run({ edge_ok: 0 })];
    const [edge] = summarizeSlos(rows);
    expect(edge.attainmentPct).toBeCloseTo(99.5);
    expect(edge.met).toBe(true);
  });

  it("empty rows yield null attainment and null met for every SLO", () => {
    for (const summary of summarizeSlos([])) {
      expect(summary.attainmentPct).toBeNull();
      expect(summary.met).toBeNull();
      expect(summary.samples).toBe(0);
    }
  });

  it("an all-NULL column yields null attainment even with rows present", () => {
    const rows = [run({ scan_fresh: null }), run({ scan_fresh: null })];
    const scan = summarizeSlos(rows).find((s) => s.key === "scan")!;
    expect(scan.attainmentPct).toBeNull();
    expect(scan.met).toBeNull();
    expect(scan.samples).toBe(0);
  });
});

describe("/api/admin/slo route", () => {
  beforeEach(() => {
    vi.resetModules();
    mockGetDb.mockReset();
  });

  it("serves bounded rows from external_probe_runs with no-store headers", async () => {
    const executed: Array<{ sql: string; args: unknown[] }> = [];
    mockGetDb.mockReturnValue({
      execute: vi.fn(async (stmt: { sql: string; args: unknown[] }) => {
        executed.push(stmt);
        return {
          rows: [
            {
              run_at: "2026-06-10T15:00:00Z",
              edge_ok: 1,
              user_path_ok: 1,
              freshness_ok: 1,
              tick_fresh: 0,
              scan_fresh: null,
              latency_ms: 312.5,
            },
          ],
        };
      }),
    });

    const { GET } = await import("../app/api/admin/slo/route");
    const res = await GET();
    const body = (await res.json()) as SloPayload;

    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toContain("no-store");
    expect(executed[0].sql).toContain("external_probe_runs");
    expect(executed[0].sql).toContain("LIMIT");
    expect(body.window_ms).toBe(SLO_WINDOW_MS);
    expect(body.missing).toBeUndefined();
    expect(body.rows).toEqual([
      {
        run_at: "2026-06-10T15:00:00Z",
        edge_ok: 1,
        user_path_ok: 1,
        freshness_ok: 1,
        tick_fresh: 0,
        scan_fresh: null,
        latency_ms: 312.5,
      },
    ]);
  });

  it("pre-migration table is a legitimate pending state: 200 + missing:true", async () => {
    mockGetDb.mockReturnValue({
      execute: vi.fn(async () => {
        throw new Error("no such table: external_probe_runs");
      }),
    });

    const { GET } = await import("../app/api/admin/slo/route");
    const res = await GET();
    const body = (await res.json()) as SloPayload;

    expect(res.status).toBe(200);
    expect(body.missing).toBe(true);
    expect(body.rows).toEqual([]);
  });
});
