/**
 * Ticker-news relevance. Providers tag headlines by keyword, so a symbol that
 * is also an English word (SPY) collects spy-agency stories. A headline is kept
 * when it names the symbol as a symbol, or never mentions the word at all (the
 * provider tagged the entity some other way).
 */

/** Listed symbols that are also common English words. Only consulted for
 *  ALL-CAPS wire copy, where case cannot separate the word from the symbol. */
const WORD_TICKERS = new Set([
  "SPY", "ALL", "ARE", "CAT", "NOW", "ON", "IT", "BIG", "FUN", "LOW", "KEY",
  "WELL", "SAFE", "TRUE", "PLAY", "LOVE", "REAL", "OPEN", "CASH", "GOOD", "HAS",
  "BEST", "EAT", "RUN", "SEE", "ONE", "CAR", "AIR", "MAIN", "PEAK", "FAST",
]);

function escape(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function isHeadlineAboutTicker(headline: string, ticker: string): boolean {
  const t = ticker.trim().toUpperCase();
  if (!t || !headline) return true;
  const e = escape(t);
  if (new RegExp(`\\$${e}\\b`, "i").test(headline)) return true;
  if (new RegExp(`\\(${e}\\)`).test(headline)) return true;
  const word = new RegExp(`(^|[^A-Za-z0-9])${e}(?![A-Za-z0-9])`, "i");
  if (!word.test(headline)) return true;
  const mixedCase = /[a-z]/.test(headline);
  if (mixedCase) return new RegExp(`(^|[^A-Za-z0-9])${e}(?![A-Za-z0-9])`).test(headline);
  return !WORD_TICKERS.has(t);
}
