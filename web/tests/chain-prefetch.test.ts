/**
 * Tests for chain prefetch logic.
 * Validates background prefetching behavior for option chain strikes.
 *
 * Tests run in node environment (no jsdom) — we test the prefetch logic
 * by validating the source code structure and pure helper behavior.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import path from "node:path";

const HOOK_PATH = path.resolve(__dirname, "../lib/useChainPrefetch.ts");
const CHAIN_TAB_PATH = path.resolve(__dirname, "../components/ticker-detail/OptionsChainTab.tsx");

describe("useChainPrefetch hook", () => {
  let hookSource: string;

  beforeEach(() => {
    hookSource = fs.readFileSync(HOOK_PATH, "utf8");
  });

  it("exports useChainPrefetch hook", () => {
    expect(hookSource).toContain("export function useChainPrefetch");
  });

  it("accepts ticker, expirations, and selectedExpiry parameters", () => {
    // Function signature
    expect(hookSource).toMatch(/function useChainPrefetch\(\s*ticker:\s*string/);
    expect(hookSource).toContain("expirations: string[]");
    expect(hookSource).toContain("selectedExpiry: string | null");
  });

  it("filters out the selected expiry from prefetch list", () => {
    expect(hookSource).toContain("exp !== selectedExpiry");
  });

  it("skips already-cached expirations", () => {
    expect(hookSource).toContain("!cacheRef.current.has(exp)");
  });

  it("uses AbortController for cleanup on unmount/re-render", () => {
    expect(hookSource).toContain("AbortController");
    expect(hookSource).toContain("controller.abort()");
    expect(hookSource).toContain("signal: controller.signal");
  });

  it("staggers requests with a delay to avoid IB pacing violations", () => {
    expect(hookSource).toContain("PREFETCH_DELAY_MS");
    expect(hookSource).toMatch(/await new Promise.*setTimeout.*PREFETCH_DELAY_MS/s);
  });

  it("limits concurrent prefetch requests", () => {
    expect(hookSource).toContain("PREFETCH_CONCURRENCY");
    expect(hookSource).toMatch(/Math\.min\(PREFETCH_CONCURRENCY/);
  });

  it("resets cache when ticker changes", () => {
    // Should have a useEffect that resets on ticker change
    expect(hookSource).toMatch(/useEffect\(\(\)\s*=>\s*\{[^}]*cacheRef\.current\s*=\s*new Map/s);
    expect(hookSource).toContain("}, [ticker])");
  });

  it("returns cacheStrikes, getCachedStrikes, prefetchedCount, totalExpirations", () => {
    expect(hookSource).toContain("cacheStrikes");
    expect(hookSource).toContain("getCachedStrikes");
    expect(hookSource).toContain("prefetchedCount");
    expect(hookSource).toContain("totalExpirations");
  });

  it("does not prefetch when there is only one expiration", () => {
    expect(hookSource).toContain("expirations.length <= 1");
  });

  it("fetches the correct API endpoint", () => {
    expect(hookSource).toContain("/api/options/chain?symbol=");
    expect(hookSource).toContain("&expiry=");
  });

  it("requests fresh option-chain responses", () => {
    expect(hookSource).toContain('cache: "no-store"');
  });

  it("handles failed fetches gracefully without crashing", () => {
    // Should have try/catch around fetch
    expect(hookSource).toMatch(/try\s*\{[^]*?fetch[^]*?catch/s);
  });
});

describe("OptionsChainTab integration with prefetch", () => {
  let tabSource: string;

  beforeEach(() => {
    tabSource = fs.readFileSync(CHAIN_TAB_PATH, "utf8");
  });

  it("imports useChainPrefetch", () => {
    expect(tabSource).toContain("useChainPrefetch");
  });

  it("uses getCachedStrikes before fetching strikes", () => {
    // Should check cache first when expiry changes
    expect(tabSource).toContain("getCachedStrikes");
  });

  it("calls cacheStrikes after successful strike fetch", () => {
    expect(tabSource).toContain("cacheStrikes");
  });

  it("requests fresh expirations and selected-expiry chain responses", () => {
    expect(tabSource).toContain("/api/options/expirations?symbol=");
    expect(tabSource).toContain("/api/options/chain?symbol=");
    expect(tabSource).toMatch(/cache:\s*"no-store"/);
  });
});

describe("Chain header sticky behavior", () => {
  let cssPath: string;
  let cssSource: string;

  beforeEach(() => {
    cssPath = path.resolve(__dirname, "../app/globals.css");
    cssSource = fs.readFileSync(cssPath, "utf8");
  });

  it("chain header uses position sticky", () => {
    expect(cssSource).toMatch(/\.chain-header[^{]*\{[^}]*position:\s*sticky/s);
  });

  it("chain grid wrapper allows vertical scrolling", () => {
    expect(cssSource).toMatch(/\.chain-grid-wrapper[^{]*\{[^}]*overflow-y:\s*(auto|scroll)/s);
  });
});
