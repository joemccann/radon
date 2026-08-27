/**
 * @vitest-environment node
 *
 * REL-093 — the P2 operability remainder.
 *
 * R-251: `computePayoffRatio` returned `{kind:"uncapped"}` on
 * `maxGainUnbounded` alone, rendered green with `data-meets-convexity="true"`
 * — an asserted Gate 1 pass with no computation behind it — and `null`
 * whenever a bound was null, which includes the window while the risk read is
 * still resolving. `showPayoff` then rendered nothing, so "Gate 1 not yet
 * measured" looked exactly like a structure that legitimately has no ratio.
 *
 * R-252: when `/orders/refresh` throws, the `finally` invalidated the cache
 * anyway and the following read hit Turso BEFORE the broker state was
 * mirrored back — repopulating the process-global 2s snapshot with the
 * pre-cancel/pre-modify book for every polling tab and every other route.
 *
 * R-256: `run()` was invoked outside any try/catch with `inFlight` already
 * true, so a synchronous throw latched the gate closed for the life of the
 * process, with no log, no backoff and no error.
 *
 * R-257: `JSON.parse(row.payload)` was unguarded, so persistence corruption
 * was filed as `DB_UNAVAILABLE` on the route and swallowed entirely on the
 * RSC seed path.
 *
 * R-263: `useOrders` had no visibility gate and no failure backoff, so a
 * backgrounded tab issued a live Turso read every 30s through the night, and
 * a 503 was retried at exactly 30s forever. Now covered behaviourally in
 * `use-orders-visibility-backoff.test.tsx`.
 *
 * R-269: the flow footer asserted "Dark Pool Lookback: 5 Trading Days" as a
 * literal beside two payload-derived numbers.
 */

import { readFileSync } from "fs";
import { join } from "path";
import { describe, expect, it, vi } from "vitest";

import { computePayoffRatio } from "../lib/order/payoffRatio";
import { createBackgroundScanTrigger } from "../lib/backgroundScan";

const ROOT = join(__dirname, "..");

function source(rel: string): string {
  return readFileSync(join(ROOT, rel), "utf-8")
    .split("\n")
    .filter((line) => {
      const t = line.trim();
      return !t.startsWith("//") && !t.startsWith("*") && !t.startsWith("/*") && !t.startsWith("{/*");
    })
    .join("\n");
}

describe("computePayoffRatio distinguishes unmeasured from absent", () => {
  it("reports an unresolved max loss as unmeasured, not as no-ratio", () => {
    expect(computePayoffRatio({ maxLoss: null, maxGain: 500 })).toEqual({ kind: "unmeasured" });
  });

  it("reports an unresolved max gain as unmeasured", () => {
    expect(computePayoffRatio({ maxLoss: 500, maxGain: null })).toEqual({ kind: "unmeasured" });
  });

  it("still reports a close-out (zero bound) as no ratio", () => {
    expect(computePayoffRatio({ maxLoss: 0, maxGain: 0 })).toBeNull();
  });

  it("computes a real ratio unchanged", () => {
    expect(computePayoffRatio({ maxLoss: 100, maxGain: 300 })).toEqual({
      kind: "ratio", ratio: 3, meetsConvexity: true,
    });
  });

  it("does not assert a convexity pass for an uncapped gain", () => {
    const src = source("lib/order/components/OrderConfirmSummary.tsx");
    expect(src).not.toContain('payoff.kind === "uncapped" ? "true"');
  });
});

describe("backgroundScan cannot latch on a synchronous throw", () => {
  it("stays open after run() throws synchronously", async () => {
    const trigger = createBackgroundScanTrigger({
      label: "TEST",
      run: () => {
        throw new Error("radonFetch built a bad URL");
      },
      backoffMs: 0,
    });
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(console, "log").mockImplementation(() => {});

    expect(trigger()).toBe(true);
    await new Promise((r) => setTimeout(r, 0));
    expect(trigger()).toBe(true);
    vi.restoreAllMocks();
  });
});

describe("order mutation routes do not cache a pre-broker read", () => {
  for (const route of ["app/api/orders/cancel/route.ts", "app/api/orders/modify/route.ts"]) {
    it(`${route} skips the read when the refresh failed`, () => {
      const src = source(route);
      expect(src).toContain("refreshed");
      expect(src).not.toMatch(/finally \{\s*invalidateOrdersSnapshotCache\(\);\s*\}/);
    });
  }
});

describe("the portfolio snapshot reader separates corruption from outage", () => {
  it("raises a distinct error for an unparseable payload", () => {
    const src = source("lib/portfolio/readPortfolioSnapshot.server.ts");
    expect(src).toContain("PortfolioSnapshotCorruptError");
    expect(src).not.toMatch(/const data = withoutPortfolioEntryDates\(JSON\.parse/);
  });
});

// R-263 (useOrders visibility gate + failure backoff) used to live here as
// three `toContain` greps over lib/useOrders.ts. They had no signal: inverting
// the gate at its use site — so the hook polls only while the tab is HIDDEN and
// the live orders book never refreshes — kept every grepped literal in place
// and all three green, as did a streak that reset on every poll. Both
// mechanisms are now mounted and observed in
// web/tests/use-orders-visibility-backoff.test.tsx.

describe("the flow footer derives its lookback", () => {
  it("no longer hardcodes the trading-day window", () => {
    const src = source("components/WorkspaceSections.tsx");
    expect(src).not.toContain("Dark Pool Lookback: 5 Trading Days");
    expect(src).toContain("darkPoolSessions");
  });
});
