/**
 * Printed yields from GS Johnstone, TMT Spec Sales, 24 Sep 2026, p. 2.
 * CoreWeave is the desk phrase "low- to mid-9%", placed on the axis so the
 * band can be drawn. It is not a printed coupon.
 */

export type YieldKind = "point" | "printed-range" | "desk-band";

export type YieldMark = {
  id: string;
  label: string;
  detail: string;
  low: number;
  high: number;
  kind: YieldKind;
};

/** Include zero so the 5% to 10% gap is not stretched. */
export const YIELD_AXIS_DOMAIN = [0, 12] as const;

export const YIELD_TICKS = [0, 2, 4, 6, 8, 10, 12] as const;

/** "Credit investors only want near-10% coupons." */
export const NEAR_TEN_BID = 10;

export const COREWEAVE_DESK_BAND = { low: 9, high: 9.5 } as const;

export const AI_CREDIT_YIELDS: readonly YieldMark[] = [
  {
    id: "softbank",
    label: "SoftBank HY",
    detail: "$11.1bn",
    low: 8.6,
    high: 9.75,
    kind: "printed-range",
  },
  {
    id: "coreweave",
    label: "CoreWeave VA",
    detail: "desk band",
    low: COREWEAVE_DESK_BAND.low,
    high: COREWEAVE_DESK_BAND.high,
    kind: "desk-band",
  },
  {
    id: "ust10",
    label: "US 10-year",
    detail: "+35 bp / 1m",
    low: 5.1,
    high: 5.1,
    kind: "point",
  },
  {
    id: "ust2",
    label: "US 2-year",
    detail: "spot",
    low: 4.9,
    high: 4.9,
    kind: "point",
  },
];

export const AI_CREDIT_DEK =
  "10-year +25 bp in two weeks, +35 bp in one month. S&P near 19x.";

export const AI_CREDIT_SOURCE =
  "Goldman Sachs, Sean Johnstone, 24 Sep 2026, p. 2. CoreWeave places the desk phrase low- to mid-9% at 9.00-9.50. Not a printed coupon.";

export function formatYield(value: number): string {
  return value.toFixed(2);
}

export function valueLabel(mark: Pick<YieldMark, "kind" | "low" | "high">): string {
  if (mark.kind === "desk-band") return "low-mid 9%";
  if (mark.kind === "point") return formatYield(mark.low);
  return `${formatYield(mark.low)}-${formatYield(mark.high)}`;
}
