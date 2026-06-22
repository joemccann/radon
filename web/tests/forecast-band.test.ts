import { describe, expect, it } from "vitest";
import {
  formatForecastBand,
  forecastBandTone,
  type ForecastScore,
} from "../lib/forecastBand";

function band(overrides: Partial<ForecastScore> = {}): ForecastScore {
  return {
    score: 0.7,
    band: { lo: 45, median: 50, hi: 70 },
    interval_width: 25,
    upside_skew: 0.4,
    convex_reinforced: true,
    horizon: 3,
    ...overrides,
  };
}

describe("formatForecastBand", () => {
  it("renders the lo–hi band around the median", () => {
    expect(formatForecastBand(band())).toBe("45.0–70.0");
  });

  it("returns an em-dash-free placeholder when no forecast", () => {
    const label = formatForecastBand(undefined);
    expect(label).toBe("--");
    expect(label).not.toContain("—");
  });

  it("rounds to one decimal", () => {
    expect(
      formatForecastBand(band({ band: { lo: 12.345, median: 20, hi: 33.987 } })),
    ).toBe("12.3–34.0");
  });
});

describe("forecastBandTone", () => {
  it("flags convex (upside) reinforcement as bull", () => {
    expect(forecastBandTone(band({ convex_reinforced: true }))).toBe("bull");
  });

  it("falls back to neutral when not reinforced", () => {
    expect(forecastBandTone(band({ convex_reinforced: false }))).toBe(
      "neutral",
    );
  });

  it("neutral when forecast missing", () => {
    expect(forecastBandTone(undefined)).toBe("neutral");
  });
});
