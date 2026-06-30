"use client";

export type FuturesQuote = {
  label: string;
  last: number | null;
  /** Prior-session settlement (IB tick 9). % change is measured from this. */
  close: number | null;
};

/**
 * Compact index-futures quote strip for the workspace header: ES / NQ / RTY
 * last price + % change from prior close. Rendered only while the CME Globex
 * session is open (the parent gates on useGlobexOpen) so it never shows stale
 * weekend/maintenance prices. Color + mono styling mirror the regime strip;
 * all colors come from brand tokens (no raw hex).
 */
export default function FuturesStrip({
  quotes,
  delayed = false,
}: {
  quotes: FuturesQuote[];
  /** Quotes are from the free Yahoo fallback (~15 min delayed), not the live
   *  relay — surface a DELAYED badge so they never read as real-time. */
  delayed?: boolean;
}) {
  if (quotes.length === 0) return null;

  return (
    <div className="futures-strip" aria-label="Index futures" suppressHydrationWarning>
      {quotes.map((q) => {
        const hasLast = q.last != null && Number.isFinite(q.last);
        const hasChange = hasLast && q.close != null && q.close > 0;
        const pct = hasChange ? ((q.last as number) - (q.close as number)) / (q.close as number) * 100 : null;
        const isUp = (pct ?? 0) >= 0;
        return (
          <div className="futures-cell" key={q.label} data-testid={`futures-${q.label}`}>
            <span className="futures-label">{q.label}</span>
            <span className="futures-last">{hasLast ? (q.last as number).toFixed(2) : "---"}</span>
            {pct != null ? (
              <span
                className="futures-chg"
                style={{ color: isUp ? "var(--positive)" : "var(--negative)" }}
              >
                {isUp ? "+" : ""}
                {pct.toFixed(2)}%
              </span>
            ) : null}
          </div>
        );
      })}
      {delayed ? (
        <span className="futures-delayed" title="Index futures delayed ~15 min">
          Delayed
        </span>
      ) : null}
    </div>
  );
}
