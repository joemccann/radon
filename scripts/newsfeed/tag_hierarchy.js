// Tag hierarchy — parent tags that should be auto-added to a post's union when
// any of their children are present. The text and vision classifiers are
// constrained to "exactly 3" free-form tags each, so we don't ask them to
// spend a slot on a generic umbrella like TECHNICAL-ANALYSIS. Instead we add
// the umbrella deterministically after the fact, applied to the union only.
//
// The classifiers' raw outputs (tags_text, tags_vision) remain untouched —
// only the displayed/filtered `tags` field gets enriched.

// Children of TECHNICAL-ANALYSIS. Includes the candlestick umbrella plus every
// specific candlestick pattern, chart pattern, technical indicator, and
// price-action concept that the system prompt enumerates.
const TECHNICAL_ANALYSIS_CHILDREN = new Set([
  // Candlestick (umbrella + specific patterns)
  "CANDLESTICK",
  "SHOOTING-STAR",
  "HAMMER",
  "INVERSE-HAMMER",
  "HANGING-MAN",
  "DOJI",
  "ENGULFING",
  "MORNING-STAR",
  "EVENING-STAR",
  "HARAMI",
  "MARUBOZU",
  "PIERCING-LINE",
  "DARK-CLOUD-COVER",
  "THREE-WHITE-SOLDIERS",
  "THREE-BLACK-CROWS",

  // Chart patterns
  "HEAD-SHOULDERS",
  "INVERSE-HEAD-SHOULDERS",
  "DOUBLE-TOP",
  "DOUBLE-BOTTOM",
  "TRIPLE-TOP",
  "TRIPLE-BOTTOM",
  "TRIANGLE",
  "ASCENDING-TRIANGLE",
  "DESCENDING-TRIANGLE",
  "FLAG",
  "PENNANT",
  "WEDGE",
  "CUP-AND-HANDLE",
  "BREAKOUT",
  "BREAKDOWN",
  "GAP",
  "ISLAND-REVERSAL",
  "CONSOLIDATION",

  // Indicators
  "RSI",
  "MACD",
  "MOVING-AVERAGE",
  "GOLDEN-CROSS",
  "DEATH-CROSS",
  "BOLLINGER-BANDS",
  "STOCHASTIC",
  "ADX",
  "ICHIMOKU",
  "FIBONACCI",
  "VWAP",
  "OBV",
  "ATR",
  "PARABOLIC-SAR",
  "KELTNER-CHANNEL",

  // Price-action / generic TA concepts.
  //
  // Excluded on purpose — these tags have a quant/factor/macro meaning that
  // dominates in this corpus; treating them as TA caused false positives:
  //   - MOMENTUM       : factor-investing tag (MoMo basket, CTA exposure).
  //   - TREND          : also used for macro / CTA trend-following.
  //   - MEAN-REVERSION : also a statistical concept, not always TA.
  //   - RANGE / PIVOT  : ambiguous outside chart context.
  // A genuinely TA-focused post still triggers via RSI / DIVERGENCE / SUPPORT /
  // RESISTANCE / a specific candlestick or chart pattern.
  "SUPPORT",
  "RESISTANCE",
  "TRENDLINE",
  "OVERSOLD",
  "OVERBOUGHT",
  "DIVERGENCE",
  "ELLIOTT-WAVE",
]);

// parent → child set. Append more entries here to enrich other hierarchies.
export const TAG_HIERARCHY = Object.freeze({
  "TECHNICAL-ANALYSIS": TECHNICAL_ANALYSIS_CHILDREN,
});

// Returns a new array with parent tags appended whenever any of their children
// are present. Idempotent — running twice yields the same result. Preserves
// input order; parents appear at the end so primary tags lead the chip pool.
export function enrichWithParentTags(tags) {
  if (!Array.isArray(tags) || tags.length === 0) return Array.isArray(tags) ? [...tags] : [];

  const present = new Set(tags);
  const result = [...tags];

  for (const [parent, children] of Object.entries(TAG_HIERARCHY)) {
    if (present.has(parent)) continue;
    for (const tag of tags) {
      if (children.has(tag)) {
        result.push(parent);
        present.add(parent);
        break;
      }
    }
  }

  return result;
}

// Test seam.
export const __TECHNICAL_ANALYSIS_CHILDREN = TECHNICAL_ANALYSIS_CHILDREN;
