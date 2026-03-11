import { describe, expect, it } from "vitest";
import { hasCompleteRvolHistory, selectPreferredCriCandidate, type CriCacheCandidate } from "../lib/criCache";

function makeHistory(length: number, withRvol: boolean) {
  return Array.from({ length }, (_, index) => ({
    date: `2026-02-${String(index + 1).padStart(2, "0")}`,
    vix: 20 + index,
    vvix: 90 + index,
    spy: 600 - index,
    cor1m: 30 + index,
    realized_vol: withRvol ? 10 + index : null,
    spx_vs_ma_pct: -1,
    vix_5d_roc: 2,
  }));
}

function makeCandidate(overrides: Partial<CriCacheCandidate> = {}): CriCacheCandidate {
  return {
    path: "/tmp/candidate.json",
    mtimeMs: Date.parse("2026-03-11T21:00:00Z"),
    data: {
      date: "2026-03-11",
      scan_time: "2026-03-11T16:00:00-04:00",
      history: makeHistory(20, true),
    },
    ...overrides,
  };
}

describe("CRI cache candidate selection", () => {
  it("recognizes a complete 20-session RVOL history", () => {
    expect(hasCompleteRvolHistory({ history: makeHistory(20, true) })).toBe(true);
    expect(hasCompleteRvolHistory({ history: makeHistory(10, false) })).toBe(false);
  });

  it("prefers the richer legacy cache when the latest scheduled file has legacy history only", () => {
    const scheduled = makeCandidate({
      path: "/tmp/cri-2026-03-11T23-59.json",
      data: {
        date: "2026-03-11",
        scan_time: "2026-03-11T16:00:00-04:00",
        history: makeHistory(10, false),
      },
    });
    const legacy = makeCandidate({
      path: "/tmp/cri.json",
      mtimeMs: Date.parse("2026-03-11T21:05:00Z"),
      data: {
        date: "2026-03-11",
        scan_time: "2026-03-11T16:05:00-04:00",
        history: makeHistory(20, true),
      },
    });

    expect(selectPreferredCriCandidate(scheduled, legacy)).toBe(legacy);
  });

  it("keeps the scheduled cache when it is at least as complete and newer", () => {
    const scheduled = makeCandidate({
      path: "/tmp/cri-2026-03-11T23-59.json",
      mtimeMs: Date.parse("2026-03-11T21:10:00Z"),
      data: {
        date: "2026-03-11",
        scan_time: "2026-03-11T16:10:00-04:00",
        history: makeHistory(20, true),
      },
    });
    const legacy = makeCandidate({
      path: "/tmp/cri.json",
      mtimeMs: Date.parse("2026-03-11T21:05:00Z"),
      data: {
        date: "2026-03-11",
        scan_time: "2026-03-11T16:05:00-04:00",
        history: makeHistory(20, true),
      },
    });

    expect(selectPreferredCriCandidate(scheduled, legacy)).toBe(scheduled);
  });
});
