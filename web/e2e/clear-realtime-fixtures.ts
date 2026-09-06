import type { Page } from "@playwright/test";
import type { DepthBook, PriceData, Trade } from "../lib/pricesProtocol";
import { optionKey, type OptionContract } from "../lib/pricesProtocol";
import { CLEAR_FIXTURE_TIME } from "./clear-fixtures";

function quote(symbol: string, last: number, close: number, delta: number | null = null): PriceData {
  return {
    symbol, last, lastIsCalculated: false, bid: last - 0.01, ask: last + 0.01,
    bidSize: 100, askSize: 100, volume: 1_000_000, high: null, low: null,
    open: null, close, week52High: null, week52Low: null, avgVolume: null,
    delta, gamma: null, theta: null, vega: null, impliedVol: null, undPrice: null,
    timestamp: CLEAR_FIXTURE_TIME,
  };
}

// Quote values match CLEAR_PORTFOLIO's cached per-share/per-contract marks.
// Real protocol frames exercise usePrices in non-demo builds without a relay.
const quotes: Record<string, PriceData> = {
  AAPL: quote("AAPL", 232.18, 230.34),
  MSFT: quote("MSFT", 525.5, 524.1),
  MSFT_20270115_530_C: { ...quote("MSFT_20270115_530_C", 9.05, 8.84, 0.4), undPrice: 525.5 },
};

const depth: DepthBook = {
  symbol: "AAPL", kind: "stock", isSmartDepth: true, feed: "SMART DEPTH",
  entitled: true, timestamp: CLEAR_FIXTURE_TIME,
  bid: [
    { price: 232.17, size: 100, marketMaker: "ARCA", exchange: "ARCA" },
    { price: 232.16, size: 200, marketMaker: "NSDQ", exchange: "NSDQ" },
    { price: 232.15, size: 150, marketMaker: "BATS", exchange: "BATS" },
  ],
  ask: [
    { price: 232.19, size: 100, marketMaker: "ARCA", exchange: "ARCA" },
    { price: 232.2, size: 200, marketMaker: "NSDQ", exchange: "NSDQ" },
    { price: 232.21, size: 150, marketMaker: "BATS", exchange: "BATS" },
  ],
};

const tape: Trade[] = [
  { price: 232.17, size: 100, exchange: "ARCA", time: String(Date.parse(CLEAR_FIXTURE_TIME) / 1_000 - 6) },
  { price: 232.18, size: 200, exchange: "NSDQ", time: String(Date.parse(CLEAR_FIXTURE_TIME) / 1_000 - 3) },
  { price: 232.19, size: 100, exchange: "ARCA", time: String(Date.parse(CLEAR_FIXTURE_TIME) / 1_000) },
];

export async function installClearRealtimeFixtures(page: Page): Promise<string[]> {
  const messages: string[] = [];
  // Only Radon's relay: never intercept development HMR or connectToServer.
  await page.routeWebSocket(/ws:\/\/(?:127\.0\.0\.1:18765|localhost:8765)|\/ws(?:\?|$)/, (socket) => {
    socket.onMessage((raw) => {
      const message = JSON.parse(raw.toString()) as {
        action: string; symbols?: string[]; contracts?: OptionContract[];
        symbol?: string; expiry?: string; right?: string; strike?: number;
      };
      messages.push(JSON.stringify(message));
      if (message.action === "subscribe" || message.action === "snapshot") {
        const symbols = [...(message.symbols ?? []), ...(message.contracts ?? []).map(optionKey)];
        const updates = Object.fromEntries(symbols.filter((symbol) => quotes[symbol]).map((symbol) => [symbol, quotes[symbol]]));
        socket.send(JSON.stringify({ type: "status", ib_connected: true, ib_issue: null, ib_status_message: null, subscriptions: symbols }));
        if (message.action === "snapshot") {
          for (const [symbol, data] of Object.entries(updates)) socket.send(JSON.stringify({ type: "snapshot", symbol, data }));
        } else {
          socket.send(JSON.stringify({ type: "batch", updates }));
        }
      }
      // A stock book must never answer an option subject with the same root.
      if (message.action === "subscribe-depth" && message.symbol === "AAPL" && !message.expiry && !message.right && message.strike == null) {
        socket.send(JSON.stringify({ type: "depth-batch", updates: { AAPL: depth } }));
        socket.send(JSON.stringify({ type: "tape-batch", updates: { AAPL: tape } }));
      }
    });
  });
  return messages;
}
