import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

/**
 * Source-inspection tests confirming RegimePanel keeps the closed-market
 * indicator while allowing websocket values to render when available.
 *
 * Tests parse component source (no DOM environment needed).
 */

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));
const PANEL_PATH = join(TEST_DIR, "../components/RegimePanel.tsx");
const source = readFileSync(PANEL_PATH, "utf-8");

describe("RegimePanel — market closed static fallback", () => {
  it("reads market_open from CriData via useRegime()", () => {
    // The component must destructure market_open (or data.market_open) from
    // the hook return value so it can gate live vs static behaviour.
    expect(source).toMatch(/market_open/);
  });

  it("disables intraday realized vol computation when market is closed", () => {
    // intradayRvol must return null when market_open === false.
    // The useMemo for intradayRvol must early-return null when !marketOpen
    // (or equivalent guard on the market_open flag).
    expect(source).toMatch(/marketOpen|market_open/);
    // The intradayRvol memo must reference the market open gate.
    const rvolMemo = source.match(/intradayRvol\s*=\s*useMemo[\s\S]*?(?=\n\s*const\s|\n\s*\/\/)/)?.[0] ?? "";
    expect(rvolMemo).toMatch(/marketOpen|market_open/);
  });

  it("shows MARKET CLOSED indicator text when market is closed", () => {
    // The panel must render an end-of-day marker when !marketOpen.
    expect(source).toMatch(/MARKET CLOSED/i);
  });

  it("does not gate VIX live badge on marketOpen", () => {
    expect(source).not.toMatch(/marketOpen\s*&&\s*hasLiveVix|marketOpen\s*\?\s*hasLiveVix/);
  });

  it("does not gate VVIX live badge on marketOpen", () => {
    expect(source).not.toMatch(/marketOpen\s*&&\s*hasLiveVvix|marketOpen\s*\?\s*hasLiveVvix/);
  });

  it("renders COR1M as a daily field, not an intraday sector proxy", () => {
    expect(source).toContain("COR1M");
    expect(source).not.toContain("SECTOR CORR");
    expect(source).not.toContain("avg_sector_correlation");
    expect(source).not.toContain("computeIntradaySectorCorr");
  });

  it("uses static data.realized_vol when market is closed (activeRvol fallback)", () => {
    // When marketOpen is false, intradayRvol is null so activeRvol falls
    // through to data?.realized_vol.  The existing fallback line handles this
    // automatically — we just need to confirm it still exists.
    expect(source).toContain("data?.realized_vol");
    expect(source).toContain("intradayRvol ??");
  });

  it("EOD indicator uses amber styling (warning color)", () => {
    // The MARKET CLOSED badge must use warning/amber colour signalling,
    // consistent with the design system.
    expect(source).toMatch(/warning|amber|#f59e0b/i);
  });

  it("crash trigger label uses COR1M > 60", () => {
    expect(source).toContain("COR1M > 60");
  });
});
