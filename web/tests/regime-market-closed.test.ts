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

  it("gates live display flags on marketOpen so LIVE badges show DAILY when market is closed", () => {
    // When market is closed, WS may still return stale "last" values (e.g.
    // Friday's close for VIX/VVIX). All display-level "live" indicators must
    // be gated on marketOpen so badges read DAILY, not LIVE.
    // The component should define effectiveHasLive* flags that combine
    // marketOpen with the raw hasLive* values.
    expect(source).toMatch(/effectiveHasLive/);
    expect(source).toMatch(/marketOpen\s*&&\s*hasLive/);
  });

  it("uses effectiveHasLive flags for LiveBadge components, not raw hasLive", () => {
    // Strip cell LiveBadge props must reference effectiveHasLive*, not hasLiveVix etc.
    // Hero label must use effectiveHasLive, not hasLive.
    expect(source).toMatch(/live=\{effectiveHasLiveVix\}/);
    expect(source).toMatch(/live=\{effectiveHasLiveVvix\}/);
    expect(source).toMatch(/live=\{effectiveHasLiveSpy\}/);
    expect(source).toMatch(/live=\{effectiveHasLiveCor1m\}/);
    expect(source).toMatch(/effectiveHasLive\s*\?/);
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

  it("crash trigger label interpolates the threshold the trigger uses", () => {
    // The literal moved into CRASH_TRIGGER_CORRELATION_THRESHOLD so the label
    // and resolveCrashTriggerState cannot disagree (T-069). The number itself
    // is pinned behaviourally in regime-market-closed-values.test.ts.
    expect(source).toContain("COR1M > ${CRASH_TRIGGER_CORRELATION_THRESHOLD}");
  });
});
