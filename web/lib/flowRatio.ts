type OptionsFlowRatioData = {
  put_call_ratio?: number | null;
  /** @deprecated Reports cached before 2026-08-07 used call premium / put premium. */
  call_put_ratio?: number | null;
  call_premium?: number | null;
  put_premium?: number | null;
};

function isNonNegativeFinite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

/**
 * Resolve the market-standard put/call premium ratio.
 *
 * Premium totals are the source of truth and keep legacy cached reports
 * correct. Canonical and reciprocal legacy fields are fallbacks for partial
 * payloads. A zero call denominator is intentionally unavailable rather than
 * serialized as Infinity.
 */
export function resolvePutCallRatio(flow: OptionsFlowRatioData): number | null {
  if (
    isNonNegativeFinite(flow.call_premium)
    && flow.call_premium > 0
    && isNonNegativeFinite(flow.put_premium)
  ) {
    return flow.put_premium / flow.call_premium;
  }

  if (isNonNegativeFinite(flow.put_call_ratio)) {
    return flow.put_call_ratio;
  }

  if (isNonNegativeFinite(flow.call_put_ratio) && flow.call_put_ratio > 0) {
    return 1 / flow.call_put_ratio;
  }

  return null;
}
