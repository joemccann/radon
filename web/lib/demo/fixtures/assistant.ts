// Request-time assistant reply for demo principals. The demo deployment holds
// no LLM subscription grant, and the operator's grants never serve trial
// users, so the panel answers from this sample instead of a model.

export const DEMO_ASSISTANT_MODEL = "radon-demo-sample";

const NOT_TICKERS = new Set(["I", "A", "AN", "THE", "AND", "OR", "VS", "IV", "PL", "ET", "AI", "ETF", "OK"]);

/** First plausible ticker in the message: an all-caps 1-5 letter word, or the word after analyze/evaluate. */
export function demoTickerFrom(text: string): string | null {
  const cue = /\b(?:analy[sz]e|evaluate|check|scan|look at)\s+\$?([a-z]{1,5})\b/i.exec(text);
  if (cue && !NOT_TICKERS.has(cue[1].toUpperCase())) return cue[1].toUpperCase();
  for (const match of text.matchAll(/\$?\b([A-Z]{1,5})\b/g)) {
    if (!NOT_TICKERS.has(match[1])) return match[1];
  }
  return null;
}

export function buildDemoAssistantReply(text: string): string {
  const ticker = demoTickerFrom(text);
  const lead = "Sample reply. The demo assistant does not call a live model, so this answer is canned.";
  if (ticker) {
    return [
      lead,
      "",
      `For ${ticker}, the full assistant would pull dark pool and OTC prints, options flow, the vol surface and your book, then run the four gates: convexity, edge, risk sizing, structure.`,
      "",
      `In this demo you can see the same inputs yourself: open ${ticker} for quotes, the chain and the Company tab, /flow-analysis/${ticker} for flow, and /regime for the market backdrop. All of it is sample data.`,
    ].join("\n");
  }
  return [
    lead,
    "",
    "The full assistant reads your positions, orders and journal, runs flow and regime scans, and drafts structures that pass the four gates before anything reaches an order ticket.",
    "",
    "Ask about a ticker, for example \"analyze NVDA\", to see where each input lives in this demo.",
  ].join("\n");
}
