import { describe, it, expect } from "vitest";
import { backfillRealizedVolHistory } from "../lib/regimeHistory";

function buildHistory() {
  return Array.from({ length: 20 }, (_, index) => ({
    date: `2026-02-${String(index + 1).padStart(2, "0")}`,
    vix: 20 + index * 0.3,
    vvix: 95 + index,
    spy: 580 + index,
    cor1m: 28 + index * 0.4,
    realized_vol: null,
    spx_vs_ma_pct: -1 + index * 0.1,
    vix_5d_roc: index * 0.5,
  }));
}

describe("backfillRealizedVolHistory", () => {
  it("rebuilds missing RVOL values for the 20-session chart from cached SPY closes", () => {
    const history = buildHistory();
    const spyCloses = Array.from({ length: 40 }, (_, index) => 560 + index * 1.5);

    const filled = backfillRealizedVolHistory(history, spyCloses);

    expect(filled).toHaveLength(20);
    expect(filled.every((entry) => typeof entry.realized_vol === "number")).toBe(true);
  });

  it("leaves existing RVOL values untouched when the cache already contains them", () => {
    const history = buildHistory();
    history[0].realized_vol = 12.34;
    const spyCloses = Array.from({ length: 40 }, (_, index) => 560 + index * 1.5);

    const filled = backfillRealizedVolHistory(history, spyCloses);

    expect(filled[0].realized_vol).toBe(12.34);
  });
});
