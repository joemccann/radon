import { describe, it, expect } from "vitest";
import {
  summarizeHostMetrics,
  cpuTone,
  memAvailTone,
  loopLagTone,
  HOST_METRICS_STALE_AFTER_MS,
  type HostMetricsRow,
} from "../lib/adminHostMetrics";

const NOW = Date.parse("2026-06-12T08:00:00Z");

function row(takenAt: string, overrides: Partial<HostMetricsRow> = {}): HostMetricsRow {
  return {
    taken_at: takenAt,
    cpu_pct: 12.5,
    mem_used_mb: 3800,
    mem_avail_mb: 3900,
    load1: 0.4,
    swap_used_mb: 0,
    loop_lag_ms: 0.2,
    units_json: null,
    ...overrides,
  };
}

describe("summarizeHostMetrics", () => {
  it("empty rows yield a null latest and stale=true", () => {
    const s = summarizeHostMetrics([], NOW);
    expect(s.latest).toBeNull();
    expect(s.stale).toBe(true);
    expect(s.cpuTrend).toEqual([]);
  });

  it("picks the newest row by taken_at even when rows arrive unordered", () => {
    const rows = [
      row("2026-06-12T07:58:17Z", { cpu_pct: 50 }),
      row("2026-06-12T07:59:17Z", { cpu_pct: 60 }),
      row("2026-06-12T07:30:17Z", { cpu_pct: 10 }),
    ];
    const s = summarizeHostMetrics(rows, NOW);
    expect(s.latest?.cpu_pct).toBe(60);
    expect(s.stale).toBe(false);
  });

  it("trend arrays are ascending by time and skip null samples", () => {
    const rows = [
      row("2026-06-12T07:59:17Z", { cpu_pct: 30, loop_lag_ms: null }),
      row("2026-06-12T07:57:17Z", { cpu_pct: 10 }),
      row("2026-06-12T07:58:17Z", { cpu_pct: 20 }),
    ];
    const s = summarizeHostMetrics(rows, NOW);
    expect(s.cpuTrend).toEqual([10, 20, 30]);
    expect(s.loopLagTrend).toEqual([0.2, 0.2]); // null sample dropped
  });

  it("a sample older than the staleness window flags stale", () => {
    const old = new Date(NOW - HOST_METRICS_STALE_AFTER_MS - 1000).toISOString();
    const s = summarizeHostMetrics([row(old)], NOW);
    expect(s.stale).toBe(true);
  });

  it("parses units_json into failed units and a restart total", () => {
    const units = JSON.stringify([
      { unit: "radon-api.service", active_state: "active", n_restarts: 2 },
      { unit: "radon-relay.service", active_state: "failed", n_restarts: 7 },
    ]);
    const s = summarizeHostMetrics([row("2026-06-12T07:59:17Z", { units_json: units })], NOW);
    expect(s.failedUnits).toEqual(["radon-relay.service"]);
    expect(s.totalRestarts).toBe(9);
  });

  it("malformed units_json degrades to no-units, never throws", () => {
    const s = summarizeHostMetrics(
      [row("2026-06-12T07:59:17Z", { units_json: "{not json" })],
      NOW,
    );
    expect(s.failedUnits).toEqual([]);
    expect(s.totalRestarts).toBeNull();
  });
});

describe("tones", () => {
  it("cpuTone thresholds", () => {
    expect(cpuTone(null)).toBe("neutral");
    expect(cpuTone(40)).toBe("positive");
    expect(cpuTone(75)).toBe("warning");
    expect(cpuTone(95)).toBe("negative");
  });

  it("memAvailTone thresholds (MB available)", () => {
    expect(memAvailTone(null)).toBe("neutral");
    expect(memAvailTone(4000)).toBe("positive");
    expect(memAvailTone(800)).toBe("warning");
    expect(memAvailTone(300)).toBe("negative");
  });

  it("loopLagTone thresholds (ms)", () => {
    expect(loopLagTone(null)).toBe("neutral");
    expect(loopLagTone(1)).toBe("positive");
    expect(loopLagTone(150)).toBe("warning");
    expect(loopLagTone(900)).toBe("negative");
  });
});
