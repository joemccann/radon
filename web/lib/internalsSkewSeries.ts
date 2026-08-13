export type SkewHistoryApiPoint = {
  date: string;
  value: number;
};

export type SkewHistorySeries = {
  ticker: string;
  expiry: string | null;
  delta: number;
  timeframe: string;
  data: SkewHistoryApiPoint[];
};

export type LongRangeSkewHistoryPayload = {
  nq?: SkewHistorySeries;
  spx?: SkewHistorySeries;
  nq_skew_history?: SkewHistorySeries;
  spx_skew_history?: SkewHistorySeries;
};

export type OptionRrSpreadPoint = {
  date: string;
  metric: "option_rr_spread";
  nq_skew: number;
  nq_option_rr: number;
  spx_option_rr: number;
  spx_skew: number;
  spx_position: null;
  nq_position: null;
};

function finiteNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** Date-align NQ and SPX option risk reversals before deriving the named spread. */
export function toLongRangeSkewPoints(
  response: LongRangeSkewHistoryPayload,
): OptionRrSpreadPoint[] {
  const nqSeries = response.nq ?? response.nq_skew_history;
  const spxSeries = response.spx ?? response.spx_skew_history;
  const nqMap = new Map<string, number>();
  const spxMap = new Map<string, number>();

  for (const entry of nqSeries?.data ?? []) {
    const value = finiteNumber(entry?.value);
    if (entry?.date && value != null) nqMap.set(entry.date, value);
  }
  for (const entry of spxSeries?.data ?? []) {
    const value = finiteNumber(entry?.value);
    if (entry?.date && value != null) spxMap.set(entry.date, value);
  }

  return [...nqMap.keys()]
    .filter((date) => spxMap.has(date))
    .sort()
    .map((date) => {
      const nqOptionRr = nqMap.get(date)!;
      const spxOptionRr = spxMap.get(date)!;
      return {
        date,
        metric: "option_rr_spread" as const,
        nq_skew: nqOptionRr - spxOptionRr,
        nq_option_rr: nqOptionRr,
        spx_option_rr: spxOptionRr,
        spx_skew: spxOptionRr,
        spx_position: null,
        nq_position: null,
      };
    });
}
