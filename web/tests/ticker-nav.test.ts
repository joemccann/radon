/**
 * Unit tests for ticker navigation, route validation, and tab URL sync.
 */
import { describe, it, expect } from "vitest";
import { resolveSectionFromPath } from "@/lib/chat";
import { isTickerRouteSegment } from "@/lib/tickerRoute";

const TICKER_RE = { test: isTickerRouteSegment };

// ─── resolveSectionFromPath ──────────────────────────────────────────────────

describe("resolveSectionFromPath — ticker-detail detection", () => {
  it("resolves /AAPL to ticker-detail", () => {
    expect(resolveSectionFromPath("/AAPL", "dashboard")).toBe("ticker-detail");
  });

  it("resolves /goog to ticker-detail (lowercase)", () => {
    expect(resolveSectionFromPath("/goog", "dashboard")).toBe("ticker-detail");
  });

  it("resolves /A to ticker-detail (single char)", () => {
    expect(resolveSectionFromPath("/A", "dashboard")).toBe("ticker-detail");
  });

  it("resolves /NVDA to ticker-detail (4 chars)", () => {
    expect(resolveSectionFromPath("/NVDA", "dashboard")).toBe("ticker-detail");
  });

  it("does not resolve /portfolio as ticker-detail", () => {
    expect(resolveSectionFromPath("/portfolio", "dashboard")).toBe("portfolio");
  });

  it("does not resolve /dashboard as ticker-detail", () => {
    expect(resolveSectionFromPath("/dashboard", "dashboard")).toBe("dashboard");
  });

  it("falls back for paths longer than 5 chars", () => {
    expect(resolveSectionFromPath("/TOOLONG", "dashboard")).toBe("dashboard");
  });

  it("falls back for numeric paths", () => {
    expect(resolveSectionFromPath("/12345", "dashboard")).toBe("dashboard");
  });

  it("resolves Cboe term-structure indices with digits to ticker-detail", () => {
    for (const path of ["/VIX3M", "/vix3m", "/VIX9D", "/VIX1D", "/VIX6M", "/VIX1Y", "/COR1M"]) {
      expect(resolveSectionFromPath(path, "dashboard"), path).toBe("ticker-detail");
    }
  });
});

// ─── TICKER_RE validation ────────────────────────────────────────────────────

describe("TICKER_RE — format validation", () => {
  it("accepts 1-5 uppercase letters", () => {
    expect(TICKER_RE.test("AAPL")).toBe(true);
    expect(TICKER_RE.test("A")).toBe(true);
    expect(TICKER_RE.test("NVDAX")).toBe(true);
  });

  it("accepts lowercase letters", () => {
    expect(TICKER_RE.test("aapl")).toBe(true);
  });

  it("rejects numbers", () => {
    expect(TICKER_RE.test("123")).toBe(false);
    expect(TICKER_RE.test("A1")).toBe(false);
  });

  it("accepts registered index symbols that carry digits", () => {
    for (const sym of ["VIX3M", "vix3m", "VIX9D", "VIX1D", "VIX6M", "VIX1Y", "COR1M", "COR3M"]) {
      expect(TICKER_RE.test(sym), sym).toBe(true);
    }
  });

  it("still rejects unregistered digit symbols of index-like shape", () => {
    expect(TICKER_RE.test("VIX12M")).toBe(false);
    expect(TICKER_RE.test("VIX1M")).toBe(false);
  });

  it("rejects more than 5 chars", () => {
    expect(TICKER_RE.test("ABCDEF")).toBe(false);
  });

  it("rejects empty string", () => {
    expect(TICKER_RE.test("")).toBe(false);
  });

  it("rejects special characters", () => {
    expect(TICKER_RE.test("AA.L")).toBe(false);
    expect(TICKER_RE.test("BRK-B")).toBe(false);
  });
});
