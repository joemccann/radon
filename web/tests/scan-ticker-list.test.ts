import { describe, expect, it } from "vitest";

import { MAX_SCAN_TICKERS, validateTickerList } from "../lib/scanTickerList";

describe("validateTickerList", () => {
  it("parses a comma-separated list into trimmed uppercase symbols", () => {
    const result = validateTickerList("nvda, amd ,TSM");
    expect(result).toEqual({ ok: true, tickers: ["NVDA", "AMD", "TSM"] });
  });

  it("accepts a single ticker with no comma", () => {
    expect(validateTickerList("nvda")).toEqual({ ok: true, tickers: ["NVDA"] });
  });

  it("dedupes by default, preserving first-seen order", () => {
    expect(validateTickerList("MU,mu,AAPL,MU")).toEqual({ ok: true, tickers: ["MU", "AAPL"] });
  });

  it("keeps duplicates when dedupe is disabled (pair scanners)", () => {
    expect(validateTickerList("NVDA,AMD,NVDA,TSM", { dedupe: false })).toEqual({
      ok: true,
      tickers: ["NVDA", "AMD", "NVDA", "TSM"],
    });
  });

  it("rejects an empty list", () => {
    const result = validateTickerList("  , ");
    expect(result.ok).toBe(false);
  });

  it("rejects malformed symbols", () => {
    const result = validateTickerList("NVDA,AM-D");
    expect(result).toEqual({ ok: false, error: "Enter 1-6 letter tickers, comma-separated." });
  });

  it("rejects symbols longer than 6 letters", () => {
    expect(validateTickerList("TOOLONGG").ok).toBe(false);
  });

  it("caps the list size", () => {
    const raw = Array.from({ length: MAX_SCAN_TICKERS + 1 }, (_, i) =>
      String.fromCharCode(65 + Math.floor(i / 26)) + String.fromCharCode(65 + (i % 26)),
    ).join(",");
    expect(validateTickerList(raw)).toEqual({
      ok: false,
      error: `Max ${MAX_SCAN_TICKERS} tickers per scan.`,
    });
  });

  it("requires an even count for pair scanners", () => {
    expect(validateTickerList("NVDA,AMD,TSM", { requirePairs: true, dedupe: false })).toEqual({
      ok: false,
      error: "Enter pairs: an even number of tickers.",
    });
    expect(validateTickerList("NVDA,AMD", { requirePairs: true, dedupe: false })).toEqual({
      ok: true,
      tickers: ["NVDA", "AMD"],
    });
  });
});
