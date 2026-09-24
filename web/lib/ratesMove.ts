/**
 * Printed rates facts from GS Johnstone, TMT Spec Sales, 24 Sep 2026, p. 2.
 * The 2-week and 1-month windows overlap, so the bars are not additive.
 * The hike odds and the year-end tightening do not share a unit.
 */

export const TEN_YEAR_MOVE = [
  { id: "month", label: "1 month", bp: 35 },
  { id: "twoweek", label: "2 weeks", bp: 25 },
] as const;

/** Headroom above the 35 bp month move. Includes zero. */
export const MOVE_AXIS_MAX = 40;

export const MOVE_TICKS = [0, 10, 20, 30, 40] as const;

export const OCTOBER_HIKE_ODDS = 71;

export const PROBABILITY_AXIS = [0, 100] as const;

/** Priced tightening by year-end. Not a percent. */
export const YEAR_END_TIGHTENING_BP = 36;

export const RATES_MOVE_SOURCE =
  "Goldman Sachs, Sean Johnstone, 24 Sep 2026, p. 2. 10-year +25 bp in two weeks and +35 bp in one month. October hike priced at 71%. About 36 bp of tightening by year-end.";
