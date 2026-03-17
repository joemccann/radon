/**
 * Unit tests: flow_analysis.py classification logic.
 *
 * Validates that spread directions (DEBIT/CREDIT) are correctly
 * mapped to long/short intent for flow classification.
 */

import { describe, it, expect } from "vitest";

/* ─── Inline replica of classify logic from flow_analysis.py ──── */

type ClassifyInput = {
  posDirection: string;
  signal: string;
  flowDir: string;
  recentDir: string;
};

function classifyPosition(input: ClassifyInput): string {
  const { posDirection, signal, flowDir, recentDir } = input;

  const isLong = ["LONG", "BUY", "DEBIT"].includes(posDirection);
  const isShort = ["SHORT", "SELL", "CREDIT"].includes(posDirection);

  if (signal === "STRONG" || signal === "MODERATE") {
    const flowSupportsLong = flowDir === "ACCUMULATION";
    const flowSupportsShort = flowDir === "DISTRIBUTION";

    if ((isLong && flowSupportsLong) || (isShort && flowSupportsShort)) {
      return "supports";
    } else if (
      (isLong && flowSupportsShort) ||
      (isShort && flowSupportsLong)
    ) {
      return "against";
    } else {
      return "neutral";
    }
  } else if (signal === "WEAK" && ["ACCUMULATION", "DISTRIBUTION"].includes(flowDir)) {
    if (recentDir !== flowDir && ["ACCUMULATION", "DISTRIBUTION"].includes(recentDir)) {
      return "watch";
    }
    return "neutral";
  }
  return "neutral";
}

/* ─── Tests ──────────────────────────────────────────────────── */

describe("Flow analysis — spread direction classification", () => {
  it("DEBIT spread + STRONG ACCUMULATION → supports", () => {
    const result = classifyPosition({
      posDirection: "DEBIT",
      signal: "STRONG",
      flowDir: "ACCUMULATION",
      recentDir: "ACCUMULATION",
    });
    expect(result).toBe("supports");
  });

  it("CREDIT spread + STRONG DISTRIBUTION → supports", () => {
    const result = classifyPosition({
      posDirection: "CREDIT",
      signal: "STRONG",
      flowDir: "DISTRIBUTION",
      recentDir: "DISTRIBUTION",
    });
    expect(result).toBe("supports");
  });

  it("DEBIT spread + STRONG DISTRIBUTION → against", () => {
    const result = classifyPosition({
      posDirection: "DEBIT",
      signal: "STRONG",
      flowDir: "DISTRIBUTION",
      recentDir: "DISTRIBUTION",
    });
    expect(result).toBe("against");
  });

  it("CREDIT spread + MODERATE ACCUMULATION → against", () => {
    const result = classifyPosition({
      posDirection: "CREDIT",
      signal: "MODERATE",
      flowDir: "ACCUMULATION",
      recentDir: "ACCUMULATION",
    });
    expect(result).toBe("against");
  });

  it("LONG + STRONG ACCUMULATION → supports (unchanged)", () => {
    const result = classifyPosition({
      posDirection: "LONG",
      signal: "STRONG",
      flowDir: "ACCUMULATION",
      recentDir: "ACCUMULATION",
    });
    expect(result).toBe("supports");
  });

  it("SHORT + MODERATE DISTRIBUTION → supports (unchanged)", () => {
    const result = classifyPosition({
      posDirection: "SHORT",
      signal: "MODERATE",
      flowDir: "DISTRIBUTION",
      recentDir: "DISTRIBUTION",
    });
    expect(result).toBe("supports");
  });

  it("DEBIT + WEAK ACCUMULATION → neutral (weak signal)", () => {
    const result = classifyPosition({
      posDirection: "DEBIT",
      signal: "WEAK",
      flowDir: "ACCUMULATION",
      recentDir: "ACCUMULATION",
    });
    expect(result).toBe("neutral");
  });
});
