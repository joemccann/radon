import { describe, expect, it } from "vitest";
import { criRow, engineCount, gexRow, markovRow, vcgRow } from "../lib/engineState";
import type { CriData } from "../lib/useRegime";
import type { MarkovStateOutput } from "../lib/useMarkovState";
import type { VcgData } from "../lib/useVcg";
import type { GexData } from "../lib/useGex";

function cri(score: number, level: string): CriData {
  return { cri: { score, level, components: { vix: 0, vvix: 0, correlation: 0, momentum: 0 } } } as CriData;
}

function markov(overrides: Partial<MarkovStateOutput>): MarkovStateOutput {
  return {
    currentBand: "LOW",
    nextLikelyBand: "ELEVATED",
    pCurrent: 0.8,
    pNext: 0.15,
    matrix: {} as MarkovStateOutput["matrix"],
    sampleSize: 59,
    ...overrides,
  };
}

function vcg(interpretation: VcgData["signal"]["interpretation"], piPanic: number): VcgData {
  return {
    scan_time: "2026-08-07T16:00:00Z",
    market_open: true,
    credit_proxy: "LQD",
    signal: { interpretation, regime: "DIVERGENCE", pi_panic: piPanic } as VcgData["signal"],
    history: [],
  };
}

function gex(overrides: Partial<GexData>): GexData {
  return {
    ticker: "SPX",
    spot: 6400,
    levels: { gex_flip: { strike: 6350, gamma: 0, distance: 50, distance_pct: 0.8 } },
    expected_range: { low: 6300, high: 6500, iv_1d: null },
    bias: { direction: "BULL", reasons: [], days_above_flip: 3, flip_migration: [] },
    ...overrides,
  } as GexData;
}

describe("criRow", () => {
  it("reads level + score with p = score/100", () => {
    const r = criRow(cri(42, "ELEVATED"));
    expect(r.name).toBe("CRI");
    expect(r.reading).toBe("ELEVATED 42");
    expect(r.p).toBeCloseTo(0.42);
    expect(r.tone).toBe("warn");
  });

  it("tones LOW core and HIGH fault", () => {
    expect(criRow(cri(12, "LOW")).tone).toBe("core");
    expect(criRow(cri(80, "CRITICAL")).tone).toBe("fault");
  });

  it("returns an empty row without data", () => {
    const r = criRow(null);
    expect(r.reading).toBeNull();
    expect(r.p).toBeNull();
    expect(r.tone).toBe("neutral");
  });
});

describe("markovRow", () => {
  it("reads the likely transition with p = pNext", () => {
    const r = markovRow(markov({}));
    expect(r.reading).toBe("LOW → ELEVATED");
    expect(r.p).toBeCloseTo(0.15);
  });

  it("falls back to HOLD current band when no transition observed", () => {
    const r = markovRow(markov({ nextLikelyBand: null, pNext: null }));
    expect(r.reading).toBe("HOLD LOW");
    expect(r.p).toBeCloseTo(0.8);
  });

  it("treats a zero-probability transition as HOLD, not a forecast", () => {
    const r = markovRow(markov({ pNext: 0 }));
    expect(r.reading).toBe("HOLD LOW");
    expect(r.p).toBeCloseTo(0.8);
  });

  it("tones p >= 0.5 core, below warn", () => {
    expect(markovRow(markov({ pNext: 0.6 })).tone).toBe("core");
    expect(markovRow(markov({ pNext: 0.15 })).tone).toBe("warn");
  });

  it("returns an empty row when history is insufficient", () => {
    const r = markovRow(markov({ currentBand: null, pCurrent: null, pNext: null, nextLikelyBand: null }));
    expect(r.reading).toBeNull();
    expect(r.p).toBeNull();
  });
});

describe("vcgRow", () => {
  it("reads the interpretation with p = pi_panic", () => {
    const r = vcgRow(vcg("NORMAL", 0.08));
    expect(r.reading).toBe("NORMAL · DIVERGENCE");
    expect(r.p).toBeCloseTo(0.08);
    expect(r.tone).toBe("core");
  });

  it("tones RISK_OFF and PANIC fault, EDR/WATCH warn", () => {
    expect(vcgRow(vcg("RISK_OFF", 0.9)).tone).toBe("fault");
    expect(vcgRow(vcg("PANIC", 0.95)).tone).toBe("fault");
    expect(vcgRow(vcg("EDR", 0.4)).tone).toBe("warn");
    expect(vcgRow(vcg("WATCH", 0.3)).tone).toBe("warn");
  });

  it("returns an empty row without data", () => {
    expect(vcgRow(null).reading).toBeNull();
  });
});

describe("gexRow", () => {
  it("reads ticker + bias with p = flip distance over expected range", () => {
    const r = gexRow(gex({}));
    expect(r.reading).toBe("SPX BULL");
    // |6400 - 6350| / (6500 - 6300) = 0.25
    expect(r.p).toBeCloseTo(0.25);
    expect(r.tone).toBe("core");
  });

  it("clamps p to 1", () => {
    const r = gexRow(gex({ spot: 7000 }));
    expect(r.p).toBe(1);
  });

  it("keeps the reading but nulls p when flip or range missing", () => {
    const noFlip = gexRow(gex({ levels: { gex_flip: null } as GexData["levels"] }));
    expect(noFlip.reading).toBe("SPX BULL");
    expect(noFlip.p).toBeNull();

    const noRange = gexRow(gex({ expected_range: { low: null, high: null, iv_1d: null } }));
    expect(noRange.p).toBeNull();
  });

  it("tones bias buckets", () => {
    expect(gexRow(gex({ bias: { direction: "CAUTIOUS_BULL", reasons: [], days_above_flip: 0, flip_migration: [] } })).tone).toBe("core");
    expect(gexRow(gex({ bias: { direction: "NEUTRAL", reasons: [], days_above_flip: 0, flip_migration: [] } })).tone).toBe("warn");
    expect(gexRow(gex({ bias: { direction: "BEAR", reasons: [], days_above_flip: 0, flip_migration: [] } })).tone).toBe("fault");
  });

  it("returns an empty row without data", () => {
    expect(gexRow(null).reading).toBeNull();
  });
});

describe("engineCount", () => {
  it("counts rows with data", () => {
    const rows = [
      criRow(cri(42, "ELEVATED")),
      markovRow(markov({ currentBand: null, pCurrent: null, pNext: null, nextLikelyBand: null })),
      vcgRow(null),
      gexRow(gex({})),
    ];
    expect(engineCount(rows)).toBe(2);
  });
});
