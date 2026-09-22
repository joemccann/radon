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
