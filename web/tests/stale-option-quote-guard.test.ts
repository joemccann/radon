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

/* ─── Extract and eval buildRestorePriceMessage ──────────────────────────── */

type PriceState = { data: Record<string, unknown> };
type RestoreMessage = { type: string; symbol: string; data: Record<string, unknown> };
type RestoreBuilder = (key: string, state: PriceState) => RestoreMessage;

// Same slice-and-eval mechanism as safeInitialState above. The builder is
// declared immediately after safeInitialState in the relay so this slice stays
// contiguous and free of module-state dependencies.
const restoreMatch = source.match(
  /const QUOTE_STALE_MS[\s\S]*?^function buildRestorePriceMessage\(key, state\) \{[\s\S]*?^\}/m,
);
// Nullable on purpose: before the restore-path guard lands this is null, and
// each test below then fails with its own explicit message instead of the whole
// file throwing at import time and taking the safeInitialState suite down too.
// eslint-disable-next-line no-new-func
const restoreBuilderOrNull: RestoreBuilder | null = restoreMatch
  ? (new Function(`${restoreMatch[0]}; return buildRestorePriceMessage;`)() as RestoreBuilder)
  : null;

function restoreBuilder(): RestoreBuilder {
  if (!restoreBuilderOrNull) {
    throw new Error(
      "buildRestorePriceMessage not found in scripts/ib_realtime_server.js — " +
        "restoreSubscriptions still broadcasts raw cached state, bypassing the stale-quote guard",
    );
  }
  return restoreBuilderOrNull;
}

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

  it("treats data just under the 8-hour threshold as NOT stale (boundary)", () => {
    // Just inside the window. Using EXACTLY hoursAgo(8) races the threshold:
    // safeInitialState recomputes Date.now() a few ms later, so age = 8h + ε,
    // which tips over the limit on a loaded CI runner (flaky). A small buffer
    // (8h − 5s) keeps it deterministically under, mirroring the "8h+1s = stale"
    // test's −2000ms buffer on the other side.
    const data = makePriceData({
      timestamp: new Date(Date.now() - (8 * 60 * 60 * 1000 - 5000)).toISOString(),
    });
    const result = safeInitialState(data);
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

/* ─── restore path (post-reconnect) ──────────────────────────────────────── */

describe("buildRestorePriceMessage — restore path routes through the stale guard", () => {
  it("nulls bid/ask on a 9h-old cached state", () => {
    const build = restoreBuilder();
    const state = { data: makePriceData({ timestamp: hoursAgo(9), lastIsCalculated: true }) };

    const message = build("SPY_20260918_740_P", state);

    expect(message.data.bid).toBeNull();
    expect(message.data.ask).toBeNull();
    expect(message.data.bidSize).toBeNull();
    expect(message.data.askSize).toBeNull();
    expect(message.data.lastIsCalculated).toBe(false);
  });

  it("passes a fresh (<8h) state through unchanged", () => {
    const build = restoreBuilder();
    const state = { data: makePriceData({ timestamp: hoursAgo(1) }) };

    const message = build("SPY_20260918_740_P", state);

    expect(message.data.bid).toBe(32.49);
    expect(message.data.ask).toBe(32.58);
    expect(message.data.bidSize).toBe(10);
    expect(message.data.askSize).toBe(10);
    // Fresh data forwards by reference — the guard makes no copy.
    expect(message.data).toBe(state.data);
  });

  it("keeps non-quote fields when nulling a stale restore", () => {
    const build = restoreBuilder();
    const state = { data: makePriceData({ timestamp: hoursAgo(14), close: 57.09, delta: -0.89 }) };

    const message = build("SPY_20260918_740_P", state);

    expect(message.data.close).toBe(57.09);
    expect(message.data.delta).toBe(-0.89);
    expect(message.data.impliedVol).toBe(0.1504);
    expect(message.data.undPrice).toBe(722.96);
  });

  it("leaves the relay's own cached state untouched", () => {
    // cleanupSymbolStateForReconnect keeps PriceData across a reconnect, so the
    // guard must sanitise the outbound copy only, never the live cache.
    const build = restoreBuilder();
    const state = { data: makePriceData({ timestamp: hoursAgo(9) }) };

    const message = build("SPY_20260918_740_P", state);

    expect(message.data).not.toBe(state.data);
    expect(state.data.bid).toBe(32.49);
    expect(state.data.ask).toBe(32.58);
  });

  it("emits the price message shape keyed by the subscription key", () => {
    const build = restoreBuilder();
    // The key comes from symbolSubscribers, not from data.symbol.
    const state = { data: makePriceData({ symbol: "STALE_KEY", timestamp: hoursAgo(1) }) };

    const message = build("VIX_20260916_20_C", state);

    expect(message.type).toBe("price");
    expect(message.symbol).toBe("VIX_20260916_20_C");
    expect(message.data).toBe(state.data);
  });

  it("shares the 8-hour threshold with the subscribe paths (boundary)", () => {
    const build = restoreBuilder();
    const justInside = build("SPY", {
      data: makePriceData({ timestamp: new Date(Date.now() - (8 * 60 * 60 * 1000 - 5000)).toISOString() }),
    });
    const justOutside = build("SPY", {
      data: makePriceData({ timestamp: new Date(Date.now() - 8 * 60 * 60 * 1000 - 2000).toISOString() }),
    });

    expect(justInside.data.bid).toBe(32.49);
    expect(justOutside.data.bid).toBeNull();
  });
});

describe("safeInitialState — present in the restore path too", () => {
  const restoreBlock = source.match(/^function restoreSubscriptions\(\) \{[\s\S]*?^\}/m)?.[0] ?? "";

  it("restoreSubscriptions broadcasts through buildRestorePriceMessage", () => {
    expect(restoreBlock).toContain("buildRestorePriceMessage(key, state)");
  });

  it("restoreSubscriptions no longer sends raw state.data", () => {
    expect(restoreBlock).not.toMatch(/data:\s*state\.data/);
  });

  it("buildRestorePriceMessage delegates to safeInitialState", () => {
    const builderBlock =
      source.match(/^function buildRestorePriceMessage\(key, state\) \{[\s\S]*?^\}/m)?.[0] ?? "";
    expect(builderBlock).toContain("safeInitialState(state.data)");
  });
});
