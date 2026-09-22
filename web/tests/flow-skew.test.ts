/**
 * describeFlowSkew: the flow report's skew block rendered as operator copy.
 *
 * The block is the Vol/Skew MR scanner's snapshot (25-delta put IV minus call
 * IV, vol points, one listed expiry nearest 30 DTE). Rising skew means puts
 * are richening against calls, which is the defensive read, so it carries the
 * negative tone; falling is the constructive read.
 */
import { describe, expect, it } from "vitest";

import { describeFlowSkew, type FlowSkew } from "@/lib/flowSkew";

const NOW = new Date("2026-09-17T15:00:00Z");

const RISING: FlowSkew = {
  expiry: "2026-10-16",
  delta: 25,
  sessions: [
    { date: "2026-09-10", value: 2.4 },
    { date: "2026-09-11", value: 2.6 },
    { date: "2026-09-16", value: 3.1 },
    { date: "2026-09-17", value: 3.42 },
  ],
  value: 3.42,
  prior: 3.1,
  change: 0.32,
  path: "rising",
  errors: [],
};

describe("describeFlowSkew", () => {
  it("formats a rising skew as puts richening with the defensive tone", () => {
    const view = describeFlowSkew(RISING, NOW);
    expect(view.available).toBe(true);
    expect(view.valueLabel).toBe("+3.42");
    expect(view.unitLabel).toBe("vol pts");
    expect(view.pathLabel).toBe("RISING");
    expect(view.tone).toBe("negative");
    expect(view.changeLabel).toBe("+0.32 vs prior session");
    expect(view.expiryLabel).toBe("Oct 16 · 29 DTE");
    expect(view.note).toMatch(/puts richening/i);
  });

  it("formats a falling skew with the constructive tone", () => {
    const view = describeFlowSkew({ ...RISING, value: 2.9, prior: 3.1, change: -0.2, path: "falling" }, NOW);
    expect(view.valueLabel).toBe("+2.90");
    expect(view.pathLabel).toBe("FALLING");
    expect(view.tone).toBe("positive");
    expect(view.changeLabel).toBe("-0.20 vs prior session");
    expect(view.note).toMatch(/puts cheapening/i);
  });

  it("reads a rising negative skew as the call premium narrowing", () => {
    const view = describeFlowSkew({ ...RISING, value: -1.1617, prior: -1.0885, change: -0.0732, path: "rising" }, NOW);
    expect(view.valueLabel).toBe("-1.16");
    expect(view.pathLabel).toBe("RISING");
    expect(view.tone).toBe("negative");
    expect(view.changeLabel).toBe("-0.07 vs prior session");
    expect(view.note).toMatch(/call premium over puts is narrowing/i);
  });

  it("keeps a flat path neutral and a negative skew signed", () => {
    const view = describeFlowSkew({ ...RISING, value: -0.8, prior: -0.85, change: 0.05, path: "flat" }, NOW);
    expect(view.valueLabel).toBe("-0.80");
    expect(view.pathLabel).toBe("FLAT");
    expect(view.tone).toBe("neutral");
    expect(view.note).toMatch(/calls richer than puts/i);
  });

  it("shows a lone session as a value without a direction", () => {
    const view = describeFlowSkew(
      { ...RISING, sessions: [{ date: "2026-09-17", value: 3.4 }], value: 3.4, prior: null, change: null, path: "unknown" },
      NOW,
    );
    expect(view.available).toBe(true);
    expect(view.valueLabel).toBe("+3.40");
    expect(view.pathLabel).toBe("NO PATH");
    expect(view.changeLabel).toBeNull();
    expect(view.tone).toBe("neutral");
    expect(view.note).toMatch(/fewer than two comparable sessions/i);
  });

  it("explains a report scanned before skew existed", () => {
    const view = describeFlowSkew(undefined, NOW);
    expect(view.available).toBe(false);
    expect(view.valueLabel).toBe("--");
    expect(view.pathLabel).toBe("PENDING");
    expect(view.expiryLabel).toBeNull();
    expect(view.note).toMatch(/refresh/i);
  });

  it("explains an unavailable snapshot without leaking diagnostics", () => {
    const view = describeFlowSkew(
      { expiry: null, delta: 25, sessions: [], value: null, prior: null, change: null, path: "unknown", errors: ["skew:UW token missing"] },
      NOW,
    );
    expect(view.available).toBe(false);
    expect(view.pathLabel).toBe("UNAVAILABLE");
    expect(view.note).not.toMatch(/token/i);
    expect(view.note).toMatch(/no listed expiry|unavailable/i);
  });
});
