/**
 * Persistent fill toast — pure logic (web/lib/fillToasts.ts).
 *
 * Covers: copy formatting (BOT/SLD → BUY/SELL, OPT strike rendering, price
 * clause), execId correction-suffix normalization, baseline priming, new-fill
 * diffing, and the bounded sessionStorage seen-set round-trip.
 */

import { describe, expect, it } from "vitest";
import type { ExecutedOrder, OrderContract } from "../lib/types";
import {
  MAX_SEEN_KEYS,
  SEEN_STORAGE_KEY,
  diffNewFills,
  execKey,
  fillGroupKey,
  formatFillToast,
  loadSeen,
  mergeFill,
  primeSeen,
  saveSeen,
} from "../lib/fillToasts";

function makeContract(overrides: Partial<OrderContract> = {}): OrderContract {
  return {
    conId: 12345,
    symbol: "EWY",
    secType: "OPT",
    strike: 175,
    right: "C",
    expiry: "20260918",
    ...overrides,
  };
}

function makeFill(overrides: Partial<ExecutedOrder> = {}): ExecutedOrder {
  return {
    execId: "0000e0d5.665.01",
    symbol: "EWY",
    contract: makeContract(overrides.contract as Partial<OrderContract> | undefined ?? {}),
    side: "SLD",
    quantity: 25,
    avgPrice: 4.55,
    commission: null,
    realizedPNL: null,
    time: new Date().toISOString(),
    exchange: "SMART",
    ...overrides,
  } as ExecutedOrder;
}

class FakeStorage {
  private store = new Map<string, string>();
  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null;
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

describe("formatFillToast", () => {
  it("formats an option SLD fill", () => {
    expect(formatFillToast(makeFill())).toBe("FILLED · SELL 25x EWY $175C @ $4.55");
  });

  it("formats a stock BOT fill", () => {
    const fill = makeFill({
      side: "BOT",
      quantity: 100,
      avgPrice: 212.3,
      symbol: "AAPL",
      contract: makeContract({ symbol: "AAPL", secType: "STK", strike: null, right: null }),
    });
    expect(formatFillToast(fill)).toBe("FILLED · BUY 100x AAPL @ $212.30");
  });

  it("renders a put strike as $175P", () => {
    const fill = makeFill({ contract: makeContract({ right: "P" }) });
    expect(formatFillToast(fill)).toBe("FILLED · SELL 25x EWY $175P @ $4.55");
  });

  it("renders fractional strikes without trailing zeros", () => {
    const fill = makeFill({ contract: makeContract({ strike: 172.5 }) });
    expect(formatFillToast(fill)).toContain("EWY $172.5C");
  });

  it("omits the price clause when avgPrice is null", () => {
    const msg = formatFillToast(makeFill({ avgPrice: null }));
    expect(msg).toBe("FILLED · SELL 25x EWY $175C");
    expect(msg).not.toContain("@");
  });

  it("omits malformed non-numeric prices without throwing", () => {
    const malformed = makeFill({ avgPrice: "4.55" as unknown as number });
    expect(() => formatFillToast(malformed)).not.toThrow();
    expect(formatFillToast(malformed)).not.toContain("@");
  });

  it("passes an unknown side through raw", () => {
    expect(formatFillToast(makeFill({ side: "XYZ" }))).toContain("FILLED · XYZ 25x");
  });

  it("never contains an em dash", () => {
    expect(formatFillToast(makeFill())).not.toMatch(/—/);
    expect(formatFillToast(makeFill({ avgPrice: null }))).not.toMatch(/—/);
  });
});

describe("execKey", () => {
  it("collapses IB correction reissues to a stable key", () => {
    const a = execKey(makeFill({ execId: "a.b.01" }));
    const b = execKey(makeFill({ execId: "a.b.02" }));
    expect(a).toBe("a.b");
    expect(a).toBe(b);
  });

  it("returns null for missing or empty execId", () => {
    expect(execKey(makeFill({ execId: "" }))).toBeNull();
    expect(execKey(makeFill({ execId: undefined as unknown as string }))).toBeNull();
  });
});

describe("primeSeen / diffNewFills", () => {
  const fills = [
    makeFill({ execId: "a.1.01" }),
    makeFill({ execId: "a.2.01" }),
    makeFill({ execId: "a.3.01" }),
  ];

  it("baseline is never toastable", () => {
    expect(diffNewFills(primeSeen(fills), fills)).toEqual([]);
  });

  it("returns exactly the unseen fill and does not re-fire once seen", () => {
    const seen = primeSeen(fills);
    const newFill = makeFill({ execId: "b.9.01" });
    const diff = diffNewFills(seen, [...fills, newFill]);
    expect(diff).toEqual([newFill]);
    // Pure: seen was not mutated by diffing.
    expect(seen.has("b.9")).toBe(false);

    const key = execKey(newFill)!;
    seen.add(key);
    expect(diffNewFills(seen, [...fills, newFill])).toEqual([]);
  });

  it("skips rows without an execId", () => {
    const seen = primeSeen(fills);
    expect(diffNewFills(seen, [...fills, makeFill({ execId: "" })])).toEqual([]);
  });

  it("collapses a correction pair within one payload to a single new fill", () => {
    const seen = primeSeen(fills);
    const original = makeFill({ execId: "c.7.01" });
    const corrected = makeFill({ execId: "c.7.02" });
    expect(diffNewFills(seen, [...fills, original, corrected])).toEqual([original]);
  });
});

describe("loadSeen / saveSeen", () => {
  it("round-trips a seen set", () => {
    const storage = new FakeStorage();
    const seen = new Set(["a.1", "a.2"]);
    saveSeen(storage, seen);
    expect(loadSeen(storage)).toEqual(seen);
  });

  it("caps persisted keys at MAX_SEEN_KEYS keeping newest", () => {
    const storage = new FakeStorage();
    const seen = new Set<string>();
    for (let i = 0; i < MAX_SEEN_KEYS + 50; i++) seen.add(`k.${i}`);
    saveSeen(storage, seen);
    const loaded = loadSeen(storage);
    expect(loaded.size).toBe(MAX_SEEN_KEYS);
    expect(loaded.has(`k.${MAX_SEEN_KEYS + 49}`)).toBe(true);
    expect(loaded.has("k.0")).toBe(false);
  });

  it("keeps the newest executions when priming a newest-first feed", () => {
    const storage = new FakeStorage();
    const newestFirst = Array.from({ length: MAX_SEEN_KEYS + 50 }, (_, index) =>
      makeFill({ execId: `fill.${MAX_SEEN_KEYS + 50 - index}.01` }));
    saveSeen(storage, primeSeen(newestFirst));
    const loaded = loadSeen(storage);
    expect(loaded.has(`fill.${MAX_SEEN_KEYS + 50}`)).toBe(true);
    expect(loaded.has("fill.1")).toBe(false);
  });

  it("returns an empty set on corrupt JSON", () => {
    const storage = new FakeStorage();
    storage.setItem(SEEN_STORAGE_KEY, "{not json");
    expect(loadSeen(storage).size).toBe(0);
  });

  it("returns an empty set when the payload is not a string array", () => {
    const storage = new FakeStorage();
    storage.setItem(SEEN_STORAGE_KEY, JSON.stringify({ nope: true }));
    expect(loadSeen(storage).size).toBe(0);
  });
});

describe("fillGroupKey", () => {
  it("groups partial fills of one order together", () => {
    expect(fillGroupKey(makeFill({ execId: "a.1.01", orderId: 42 }))).toBe(
      fillGroupKey(makeFill({ execId: "a.2.01", orderId: 42 })),
    );
  });

  it("prefers permId over a recycled orderId (R-642)", () => {
    // IB orderIds recycle per session/clientId; permId is stable. An afternoon
    // order reusing a morning orderId must not merge into the morning total.
    const morning = makeFill({ execId: "r.1.01", orderId: 42, permId: 111 });
    const afternoon = makeFill({ execId: "r.2.01", orderId: 42, permId: 222 });
    expect(fillGroupKey(morning)).not.toBe(fillGroupKey(afternoon));
    expect(fillGroupKey(morning)).toBe(
      fillGroupKey(makeFill({ execId: "r.3.01", orderId: 43, permId: 111 })),
    );
  });

  it("separates distinct orders", () => {
    expect(fillGroupKey(makeFill({ orderId: 42 }))).not.toBe(
      fillGroupKey(makeFill({ orderId: 43 })),
    );
  });

  it("falls back to instrument and side when the row carries no order id", () => {
    expect(fillGroupKey(makeFill())).toBe(fillGroupKey(makeFill({ execId: "z.9.01" })));
    expect(fillGroupKey(makeFill())).not.toBe(fillGroupKey(makeFill({ side: "BOT" })));
  });
});

describe("mergeFill", () => {
  it("returns the fill unchanged when nothing is running yet", () => {
    const fill = makeFill({ quantity: 10, avgPrice: 4.5 });
    expect(mergeFill(null, fill)).toBe(fill);
  });

  it("sums quantity and share-weights the price", () => {
    const merged = mergeFill(
      makeFill({ quantity: 10, avgPrice: 4.5 }),
      makeFill({ quantity: 15, avgPrice: 4.6 }),
    );
    expect(merged.quantity).toBe(25);
    expect(merged.avgPrice).toBeCloseTo(4.56, 10);
    expect(formatFillToast(merged)).toBe("FILLED · SELL 25x EWY $175C @ $4.56");
  });

  it("keeps the reported price when the other side has none", () => {
    const merged = mergeFill(
      makeFill({ quantity: 10, avgPrice: null }),
      makeFill({ quantity: 5, avgPrice: 4.2 }),
    );
    expect(merged.quantity).toBe(15);
    expect(merged.avgPrice).toBeCloseTo(4.2, 10);
  });

  it("yields a null price when neither fill reports one", () => {
    const merged = mergeFill(
      makeFill({ quantity: 10, avgPrice: null }),
      makeFill({ quantity: 5, avgPrice: null }),
    );
    expect(merged.avgPrice).toBeNull();
    expect(formatFillToast(merged)).toBe("FILLED · SELL 15x EWY $175C");
  });
});
