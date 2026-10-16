/**
 * Path-segment validation for the `/[ticker]` workspace route.
 *
 * Equity tickers are 1-5 letters. Index symbols come from the shared
 * registry so Cboe names that carry digits (VIX3M, VIX9D, COR1M, …) reach
 * the same page as VIX or SPX instead of 404ing on the letters-only rule.
 */
import { isIndexSymbol } from "./indexSymbols";

const EQUITY_TICKER_RE = /^[A-Za-z]{1,5}$/;

export function isTickerRouteSegment(raw: string): boolean {
  return EQUITY_TICKER_RE.test(raw) || isIndexSymbol(raw);
}

/**
 * Canonical href for the `/[ticker]` route.
 *
 * Encoding the segment is what keeps the push on this origin: an unencoded
 * segment that begins with `/` builds `//host`, a protocol-relative URL the
 * browser resolves off-origin before `isTickerRouteSegment` above ever runs.
 * Every segment this route accepts is alphanumeric, so encoding leaves each of
 * them byte-identical and only reshapes input the route already 404s.
 */
export function tickerHref(ticker: string, positionId?: number | null): string {
  const path = `/${encodeURIComponent(ticker.toUpperCase())}`;
  return positionId != null ? `${path}?posId=${positionId}` : path;
}
