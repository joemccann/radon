// F11 — forecast-driven scan scoring (render side).
//
// The Python scanner attaches a `forecast` block to each top_signal: a
// Chronos-2 quantile band over the ticker's recent flow_strength plus a
// convexity flag. These pure helpers turn that block into the band label
// and tone the OpportunitiesCard renders. Pure + tested so the card can stay
// a thin presentational shell.

export type ForecastScore = {
  score: number;
  band: { lo: number; median: number; hi: number };
  interval_width: number;
  upside_skew: number;
  convex_reinforced: boolean;
  horizon: number;
};

// ASCII placeholder — no em dash in user-facing copy.
const NO_FORECAST = "--";

function oneDecimal(value: number): string {
  return value.toFixed(1);
}

/** "lo–hi" band label, or an ASCII placeholder when no forecast is present. */
export function formatForecastBand(forecast: ForecastScore | undefined): string {
  if (!forecast) return NO_FORECAST;
  const { lo, hi } = forecast.band;
  return `${oneDecimal(lo)}–${oneDecimal(hi)}`;
}

/** Color tone: a convex-reinforced (upside-skewed) band reads as bull. */
export function forecastBandTone(
  forecast: ForecastScore | undefined,
): "bull" | "neutral" {
  if (!forecast) return "neutral";
  return forecast.convex_reinforced ? "bull" : "neutral";
}
