import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const webDir = resolve(__dirname, "..");
const projectRoot = resolve(webDir, "..");
const source = readFileSync(resolve(projectRoot, "scripts", "ib_realtime_server.js"), "utf8");

/* ─── Extract and eval safeInitialState ─────────────────────────────────── */

// Pull safeInitialState from source so we test the real implementation.
const fnMatch = source.match(
  /const QUOTE_STALE_MS[\s\S]*?^function safeInitialState\(data\) \{[\s\S]*?^\}/m,
);
if (!fnMatch) throw new Error("Could not locate safeInitialState in ib_realtime_server.js");

// eslint-disable-next-line no-new-func
const safeInitialState: (data: Record<string, unknown>) => Record<string, unknown> = new Function(
  `${fnMatch[0]}; return safeInitialState;`,
)();

/* ─── helpers ────────────────────────────────────────────────────────────── */

function makePriceData(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    symbol: "SPY_20260918_740_P",
    last: 32.53,
    lastIsCalculated: false,
    bid: 32.49,
    ask: 32.58,
    bidSize: 10,
    askSize: 10,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: 32.50,
    delta: -0.56,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: 0.1504,
    undPrice: 722.96,
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

function hoursAgo(n: number): string {
  return new Date(Date.now() - n * 60 * 60 * 1000).toISOString();
}

/* ─── safeInitialState ───────────────────────────────────────────────────── */

describe("safeInitialState — stale bid/ask guard", () => {
  it("passes through recent data unchanged", () => {
    const data = makePriceData({ timestamp: hoursAgo(1) });
    const result = safeInitialState(data);
    expect(result.bid).toBe(32.49);
    expect(result.ask).toBe(32.58);
    expect(result.bidSize).toBe(10);
    expect(result.askSize).toBe(10);
  });

  it("nulls out bid/ask when timestamp is older than 8 hours", () => {
    const data = makePriceData({ timestamp: hoursAgo(9) });
    const result = safeInitialState(data);
    expect(result.bid).toBeNull();
    expect(result.ask).toBeNull();
    expect(result.bidSize).toBeNull();
    expect(result.askSize).toBeNull();
  });

  it("preserves non-quote fields when nulling stale bid/ask", () => {
    const data = makePriceData({ timestamp: hoursAgo(10), close: 57.09, delta: -0.89 });
    const result = safeInitialState(data);
    // Quote fields cleared
    expect(result.bid).toBeNull();
    expect(result.ask).toBeNull();
    // Non-quote fields preserved
    expect(result.close).toBe(57.09);
    expect(result.delta).toBe(-0.89);
    expect(result.symbol).toBe("SPY_20260918_740_P");
  });

  it("sets lastIsCalculated false when nulling bid/ask so mid cannot be derived", () => {
    const data = makePriceData({ timestamp: hoursAgo(12), lastIsCalculated: true });
    const result = safeInitialState(data);
    expect(result.bid).toBeNull();
    expect(result.ask).toBeNull();
    expect(result.lastIsCalculated).toBe(false);
  });

  it("returns original data reference when not stale (no copy overhead)", () => {
    const data = makePriceData({ timestamp: hoursAgo(0.5) });
    const result = safeInitialState(data);
    // Should be the same object (no defensive copy needed for fresh data)
    expect(result).toBe(data);
  });

  it("returns a shallow copy (not same reference) when stale, leaving original intact", () => {
    const data = makePriceData({ timestamp: hoursAgo(20) });
    const result = safeInitialState(data);
    expect(result).not.toBe(data);
    // Original data unchanged
    expect(data.bid).toBe(32.49);
  });

  it("handles missing timestamp gracefully — returns data as-is", () => {
    const data = makePriceData({ timestamp: undefined });
    const result = safeInitialState(data);
    expect(result).toBe(data);
  });

  it("treats exactly 8-hour-old data as NOT stale (boundary)", () => {
    // Exactly at the threshold should still pass through
    const data = makePriceData({ timestamp: hoursAgo(8) });
    const result = safeInitialState(data);
    // Age is ~8h (slightly under due to execution time) → not stale
    expect(result.bid).toBe(32.49);
  });

  it("treats 8h+1s data as stale (boundary)", () => {
    const data = makePriceData({
      timestamp: new Date(Date.now() - 8 * 60 * 60 * 1000 - 2000).toISOString(),
    });
    const result = safeInitialState(data);
    expect(result.bid).toBeNull();
  });
});

describe("safeInitialState — present in all three subscribe paths", () => {
  it("stock subscribe path uses safeInitialState", () => {
    // The stock subscribe block must call safeInitialState before the initial price send
    const stockBlock = source.match(
      /\/\/ Stock subscriptions[\s\S]*?subscribed\.push\(symbol\)/,
    )?.[0] ?? "";
    expect(stockBlock).toContain("safeInitialState(state.data)");
  });

  it("option contract subscribe path uses safeInitialState", () => {
    const optionBlock = source.match(
      /\/\/ Option contract subscriptions[\s\S]*?subscribed\.push\(key\)/,
    )?.[0] ?? "";
    expect(optionBlock).toContain("safeInitialState(state.data)");
  });

  it("index subscribe path uses safeInitialState", () => {
    const indexBlock = source.match(
      /\/\/ Index subscriptions[\s\S]*?subscribed\.push\(key\)/,
    )?.[0] ?? "";
    expect(indexBlock).toContain("safeInitialState(state.data)");
  });

  it("QUOTE_STALE_MS is defined as 8 hours in milliseconds", () => {
    expect(source).toContain("const QUOTE_STALE_MS = 8 * 60 * 60 * 1000");
  });
});
