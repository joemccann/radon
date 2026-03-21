/**
 * Unit tests for ticker navigation, route validation, and tab URL sync.
 */
import { describe, it, expect } from "vitest";
import { resolveSectionFromPath } from "@/lib/chat";

// ─── Route validation constants (mirrored from [ticker]/page.tsx) ────────────

const RESERVED = new Set([
  "api", "dashboard", "flow-analysis", "portfolio", "performance",
  "orders", "scanner", "discover", "journal", "regime", "internals", "cta", "kit",
  "_next", "favicon",
]);

const TICKER_RE = /^[A-Za-z]{1,5}$/;

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

// ─── RESERVED set ────────────────────────────────────────────────────────────

describe("RESERVED — blocks reserved paths", () => {
  it("blocks api", () => {
    expect(RESERVED.has("api")).toBe(true);
  });

  it("blocks internals", () => {
    expect(RESERVED.has("internals")).toBe(true);
  });

  it("blocks dashboard", () => {
    expect(RESERVED.has("dashboard")).toBe(true);
  });

  it("blocks portfolio", () => {
    expect(RESERVED.has("portfolio")).toBe(true);
  });

  it("blocks _next", () => {
    expect(RESERVED.has("_next")).toBe(true);
  });

  it("does not block valid tickers", () => {
    expect(RESERVED.has("aapl")).toBe(false);
    expect(RESERVED.has("goog")).toBe(false);
  });
});

// ─── Tab URL validation ──────────────────────────────────────────────────────

describe("Tab URL sync logic", () => {
  const VALID_TABS = new Set(["company", "book", "chain", "position", "order", "news", "ratings", "seasonality"]);

  it("validates known tabs", () => {
    expect(VALID_TABS.has("chain")).toBe(true);
    expect(VALID_TABS.has("ratings")).toBe(true);
  });

  it("rejects unknown tabs — falls back to company", () => {
    const rawTab = "invalid";
    const activeTab = rawTab && VALID_TABS.has(rawTab) ? rawTab : "company";
    expect(activeTab).toBe("company");
  });

  it("null tab falls back to company", () => {
    const rawTab: string | null = null;
    const activeTab = rawTab && VALID_TABS.has(rawTab) ? rawTab : "company";
    expect(activeTab).toBe("company");
  });

  it("company tab produces no ?tab= param", () => {
    const params = new URLSearchParams();
    const tab = "company";
    if (tab === "company") {
      params.delete("tab");
    } else {
      params.set("tab", tab);
    }
    expect(params.toString()).toBe("");
  });

  it("chain tab produces ?tab=chain", () => {
    const params = new URLSearchParams();
    const tab = "chain";
    if (tab === "company") {
      params.delete("tab");
    } else {
      params.set("tab", tab);
    }
    expect(params.toString()).toBe("tab=chain");
  });
});
