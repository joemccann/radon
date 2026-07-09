/**
 * Centralized tooltip dictionary for every section title in the app.
 * Each entry explains what the data is, how it's derived, and why it matters.
 */

export const SECTION_TOOLTIPS: Record<string, string> = {
  /* ── Flow Analysis ───────────────────────────────────── */

  "Flow Supports Position":
    "Positions where dark pool flow direction matches your trade thesis. " +
    "Derived from Unusual Whales dark pool prints — accumulation flow confirms long bias, " +
    "distribution confirms short bias. Supporting flow increases conviction to hold or add.",

  "Flow Against Position":
    "Positions where institutional dark pool activity contradicts your trade direction. " +
    "Distribution flow against a long position may signal smart money exiting. " +
    "Contradicting flow is a risk signal — consider reducing size or tightening stops.",

  "Neutral / Low Signal":
    "Positions with no clear institutional flow signal — dark pool activity is balanced " +
    "or volume is too low to draw conclusions. These positions rely on thesis alone " +
    "without flow confirmation. Monitor for emerging signals.",

  "Watch Closely":
    "Positions flagged for elevated risk — flow may be shifting, approaching key levels, " +
    "or showing early signs of institutional repositioning. These warrant daily review " +
    "and potential action if conditions deteriorate.",

  /* ── Portfolio ───────────────────────────────────────── */

  "Defined Risk Positions":
    "Options positions with capped maximum loss — vertical spreads, long options, or " +
    "debit structures where loss is limited to premium paid or spread width minus credit. " +
    "All positions must pass Gate 1 (convexity: gain >= 2x loss). P&L uses IB mark-to-market.",

  "Undefined Risk Positions":
    "Positions with theoretically unlimited or very large potential loss — naked shorts, " +
    "short strangles, or ratio spreads. These require heightened monitoring and strict " +
    "sizing discipline. Max 2.5% bankroll per position via Kelly criterion.",

  "Equity Positions":
    "Stock and ETF positions — long or short equity without options overlay. " +
    "Daily P&L = (last - prior close) x quantity. Total P&L = market value minus entry cost. " +
    "These carry directional equity risk without the convexity properties of options.",

  /* ── Scanner ─────────────────────────────────────────── */

  "Scanner Signals":
    "Dark pool flow scanner results from the watchlist. Score = composite of flow strength, " +
    "buy ratio, and sustained activity. Direction indicates accumulation (bullish) or " +
    "distribution (bearish). Prints = number of dark pool transactions. " +
    "Sustained days measures how long the signal has persisted.",

  /* ── Discover ────────────────────────────────────────── */

  "Discovery Candidates":
    "Market-wide options flow scan for new trade candidates not on the watchlist. " +
    "Ranked by discovery score (0-100): 60+ = strong (evaluate immediately), " +
    "40-59 = monitor, <40 = weak signal. Bias derived from put/call ratio and " +
    "flow side (ask-side = buying pressure, bid-side = selling pressure).",

  /* ── Journal ─────────────────────────────────────────── */

  "Trade Journal":
    "Append-only execution log from the Turso journal. Each entry records structure, " +
    "Kelly sizing, gate pass/fail status, and edge analysis. Return on Risk = " +
    "Realized P&L / Capital at Risk (debit paid or spread width minus credit). " +
    "Use this to audit decision quality and identify pattern drift.",

  /* ── Orders ──────────────────────────────────────────── */

  "Open Orders":
    "Pending orders in IB Gateway — limit orders, stop-limits, and pending manual entries. " +
    "Last Price shows the current market mid for the contract. Orders can be modified " +
    "(price/quantity) or cancelled directly. Status reflects IB order state " +
    "(PreSubmitted, Submitted, Filled, Cancelled).",

  "Today's Executed Orders":
    "Today's fills and cancellations from IB. Avg Fill Price = volume-weighted average " +
    "across partial fills. Commission = total IB fees. Realized P&L is reported by IB " +
    "on closing transactions only. Use this to verify fill quality vs limit price.",

  "Historical Trades (30 Days)":
    "Trade blotter derived from the Turso journal (live fill writers + rehydrate). " +
    "Shows cost basis, proceeds, realized P&L, and commission per completed or open trade. " +
    "Session fills on Today's Executed may land here after journal_sync / fill_monitor " +
    "with open/close labels; use Refresh to re-pull. Not a live IB session blotter.",

  /* ── Regime / CRI ────────────────────────────────────── */

  "CRI COMPONENTS":
    "Crash Risk Index broken into 4 sub-scores (0-25 each, 100 total). " +
    "VIX and VVIX measure implied vol stress. Correlation tracks COR1M implied herding in the largest S&P 500 stocks. " +
    "Momentum captures SPX trend breakdown. Live values update from WS during market hours.",

  "CRASH TRIGGER CONDITIONS":
    "Three simultaneous conditions that signal a potential crash regime: " +
    "SPX below 100-day MA (trend break), realized vol > 25% (historical stress), " +
    "and COR1M > 60 (panic herding). All three must fire to trigger.",

  "20-SESSION HISTORY":
    "Left chart tracks VIX and VVIX across the last 20 trading sessions so you can see whether volatility and vol-of-vol are expanding together or cooling off. " +
    "Right chart compares realized volatility with COR1M over the same window to show whether actual market turbulence is being joined by tighter stock-to-stock correlation. " +
    "The latest point is the most recent session in view, and each point shows that session's reading.",

  "RELATIONSHIP VIEW":
    "Relationship-first analytics for COR1M and realized volatility. " +
    "The spread panel shows the correlation risk premium (COR1M minus RVOL), the quadrant view classifies the latest session by realized volatility versus implied correlation, " +
    "and the normalized overlay compares their z-scores so divergence is visible on one scale.",

  "NORMALIZED DIVERGENCE":
    "20-session z-score overlay for RVOL and COR1M. " +
    "Both series are normalized to the same scale so you can compare relative stretch instead of raw units. " +
    "A positive gap means COR1M is richer than RVOL on a normalized basis: reduce gross exposure, keep or add index hedges, and avoid pressing short-vol beta until realized stress confirms. " +
    "A negative gap means realized volatility is leading implied correlation: harvest crash hedges into strength, lean back toward single-name expression or dispersion, and only add market beta if the gap starts to mean-revert.",

  /* ── CTA Page ────────────────────────────────────────── */

  "VOL-TARGETING MODEL":
    "CTA vol-targeting exposure estimate from CRI scan. Implied Exposure = " +
    "target vol / realized vol (100% = full allocation). Forced Reduction = " +
    "the % CTAs must sell when vol spikes above target. Est. Selling = " +
    "notional CTA selling in billions. Low exposure = systematic deleveraging underway.",

  "MENTHORQ CTA POSITIONING":
    "Institutional CTA positioning data from MenthorQ. Shows normalized positions " +
    "(-2 to +2 scale) for trend-following funds across asset classes. " +
    "Fetched daily post-close via Playwright + Vision extraction from MenthorQ charts.",

  /* ── CTA Table Section Labels ────────────────────────── */

  "MAIN INDICES":
    "CTA positioning in major equity indices (S&P 500, Nasdaq 100, etc). " +
    "Position Today vs Yesterday shows daily shift. Percentiles rank current positioning " +
    "over 1M/3M/1Y windows. Z-Score measures standard deviations from 3-month mean — " +
    "below -1.5 signals extreme underweight (potential forced selling).",

  "INDEX FUTURES":
    "CTA positioning in broader index futures. Same methodology as Main Indices " +
    "but covers additional contracts. Low percentiles + negative z-scores indicate " +
    "CTAs are underweight — potential for forced selling if vol rises further.",

  "COMMODITIES":
    "CTA positioning in commodity futures (crude oil, gold, etc). " +
    "Commodity CTAs adjust position size based on trailing realized vol. " +
    "Extreme z-scores signal crowded trades — reversals can be sharp.",

  "CURRENCIES":
    "CTA positioning in FX futures. Currency trend followers tend to build " +
    "large positions slowly and unwind quickly. Low percentile + negative z-score " +
    "flags currencies where CTA selling pressure may accelerate.",
};
