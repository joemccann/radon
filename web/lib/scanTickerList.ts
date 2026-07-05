/**
 * Comma-separated ticker-list parsing + validation for scan-by-ticker
 * inputs (LEAP / GARCH scanner tabs and their Next.js scan routes).
 * Mirrors the FastAPI-side rules in scripts/api/server.py:_parse_scan_tickers.
 */

export const MAX_SCAN_TICKERS = 25;

const TICKER_SYMBOL_RE = /^[A-Z]{1,6}$/;

export type TickerListOptions = {
  /** Pair scanners (GARCH) need an even symbol count. */
  requirePairs?: boolean;
  /** Dedupe by default; pair scanners keep duplicates (order defines pairs). */
  dedupe?: boolean;
};

export type TickerListResult =
  | { ok: true; tickers: string[] }
  | { ok: false; error: string };

export function parseTickerListInput(raw: string): string[] {
  return raw
    .split(",")
    .map((token) => token.trim().toUpperCase())
    .filter((token) => token.length > 0);
}

export function validateTickerList(
  raw: string,
  options: TickerListOptions = {},
): TickerListResult {
  const { requirePairs = false, dedupe = true } = options;
  let tickers = parseTickerListInput(raw);
  if (dedupe) tickers = [...new Set(tickers)];
  if (tickers.length === 0) {
    return { ok: false, error: "Enter at least one ticker." };
  }
  if (tickers.some((ticker) => !TICKER_SYMBOL_RE.test(ticker))) {
    return { ok: false, error: "Enter 1-6 letter tickers, comma-separated." };
  }
  if (tickers.length > MAX_SCAN_TICKERS) {
    return { ok: false, error: `Max ${MAX_SCAN_TICKERS} tickers per scan.` };
  }
  if (requirePairs && tickers.length % 2 !== 0) {
    return { ok: false, error: "Enter pairs: an even number of tickers." };
  }
  return { ok: true, tickers };
}

/** Normalise a scan-route body's `tickers` field (string or array) to raw text. */
export function tickersBodyToRaw(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.filter((item) => typeof item === "string").join(",");
  return "";
}
