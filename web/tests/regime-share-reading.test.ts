/**
 * The share reading is exactly what /regime/cri is showing.
 * 2026-11-19: page CRI 51 (live VIX 28.54, COR1M 30.70); cards 48 (snapshot).
 */
import { describe, expect, it } from "vitest";
import { buildRegimeShareReading } from "../lib/regimeShareReading";

const liveCri = { score: 51.03, level: "HIGH", components: { vix: 16.5, vvix: 12.8, correlation: 7.4, momentum: 14.3 } };

const shown = {
  liveCri,
  vix: 28.54,
  vvix: 127.56,
  spy: 716.52,
  cor1m: 30.7,
  cor1m5dChange: 13.04,
  vvixVixRatio: 4.47,
  spxDistancePct: -5.72,
  realizedVol: 23.48,
  spxBelowMa: true,
  cor1mAboveThreshold: false,
};

describe("buildRegimeShareReading", () => {
  it("carries the page's live CRI and the inputs it was computed from", () => {
    expect(buildRegimeShareReading(shown)).toEqual({
      vix: 28.54,
      vvix: 127.56,
      spy: 716.52,
      cor1m: 30.7,
      cor1m_5d_change: 13.04,
      vvix_vix_ratio: 4.47,
      spx_distance_pct: -5.72,
      realized_vol: 23.48,
      cri: liveCri,
      crash_trigger: { conditions: { spx_below_100d_ma: true, cor1m_gt_60: false } },
    });
  });

  it("is null when the page is showing the scan snapshot", () => {
    expect(buildRegimeShareReading({ ...shown, liveCri: null })).toBeNull();
  });

  it("drops readings the page does not have", () => {
    const reading = buildRegimeShareReading({ ...shown, realizedVol: null, spy: Number.NaN });
    expect(reading).not.toHaveProperty("realized_vol");
    expect(reading).not.toHaveProperty("spy");
  });
});
