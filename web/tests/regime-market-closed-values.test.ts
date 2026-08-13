/**
 * Unit tests: RegimePanel COR1M presentation + market-closed value gating
 *
 * Regression target:
 *  1. The regime strip must use COR1M fields from CRI data, not sector ETF proxies.
 *  2. The component must no longer depend on intraday sector-correlation snapshots.
 *  3. VIX/VVIX/SPY live values and timestamps refresh from WS updates.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";
import { resolveCrashTriggerState, resolveRegimeStripLiveState } from "../lib/regimeLiveStrip";
import type { PriceData } from "../lib/pricesProtocol";

// Minimal price entry — resolveRegimeStripLiveState only reads .last and .close.
function px(last: number, close = last - 1): PriceData {
  return { last, close } as unknown as PriceData;
}

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));
const PANEL_PATH = join(TEST_DIR, "../components/RegimePanel.tsx");
const HELPER_PATH = join(TEST_DIR, "../lib/regimeLiveStrip.ts");
const panelSource = readFileSync(PANEL_PATH, "utf-8");
const helperSource = readFileSync(HELPER_PATH, "utf-8");

describe("RegimePanel — COR1M replaces sector ETF correlation inputs", () => {
  it("renders COR1M instead of SECTOR CORR", () => {
    expect(panelSource).toContain("COR1M");
    expect(panelSource).not.toContain("SECTOR CORR");
  });

  it("reads COR1M fields from CRI data", () => {
    expect(helperSource).toContain("data?.cor1m");
    expect(helperSource).toContain("data?.cor1m_5d_change");
    expect(panelSource).not.toContain("avg_sector_correlation");
  });

  it("does not depend on intraday sector correlation utilities", () => {
    expect(panelSource).not.toContain("computeIntradaySectorCorr");
    expect(panelSource).not.toContain("appendSnapshot");
    expect(panelSource).not.toContain("bufferDepth");
    expect(panelSource).not.toContain("resetBuffer");
  });

  it("uses COR1M > 60 for the crash-trigger label", () => {
    expect(panelSource).toContain("COR1M > 60");
  });
});

describe("resolveRegimeStripLiveState — prefers live WS values, gated on market-open", () => {
  // Behavioral replacement for the former source-text greps: exercise the pure
  // resolver directly so a refactor that preserves behavior keeps tests green,
  // and the market-open gate (which the greps never checked) is actually pinned.
  it("vixValue uses the live WS value when market is open", () => {
    const s = resolveRegimeStripLiveState({ prices: { VIX: px(22.5) }, data: { vix: 19 }, marketOpen: true });
    expect(s.liveVix).toBe(22.5);
    expect(s.vixValue).toBe(22.5);
    expect(s.hasLiveVix).toBe(true);
  });

  it("vixValue falls back to CRI data.vix when market is closed", () => {
    const s = resolveRegimeStripLiveState({ prices: { VIX: px(22.5) }, data: { vix: 19 }, marketOpen: false });
    expect(s.liveVix).toBeNull();
    expect(s.vixValue).toBe(19);
    expect(s.hasLiveVix).toBe(false);
  });

  it("vvixValue prefers live WS open, falls back to data.vvix closed", () => {
    const open = resolveRegimeStripLiveState({ prices: { VVIX: px(95) }, data: { vvix: 88 }, marketOpen: true });
    expect(open.vvixValue).toBe(95);
    expect(open.hasLiveVvix).toBe(true);
    const closed = resolveRegimeStripLiveState({ prices: { VVIX: px(95) }, data: { vvix: 88 }, marketOpen: false });
    expect(closed.vvixValue).toBe(88);
    expect(closed.hasLiveVvix).toBe(false);
  });

  it("spyValue prefers live WS open, falls back to data.spy closed", () => {
    const open = resolveRegimeStripLiveState({ prices: { SPY: px(530) }, data: { spy: 525 }, marketOpen: true });
    expect(open.spyValue).toBe(530);
    expect(open.hasLiveSpy).toBe(true);
    const closed = resolveRegimeStripLiveState({ prices: { SPY: px(530) }, data: { spy: 525 }, marketOpen: false });
    expect(closed.spyValue).toBe(525);
    expect(closed.hasLiveSpy).toBe(false);
  });

  it("falls back to data values when no live price exists even with market open", () => {
    const s = resolveRegimeStripLiveState({ prices: {}, data: { vix: 19, vvix: 88, spy: 525 }, marketOpen: true });
    expect(s.vixValue).toBe(19);
    expect(s.vvixValue).toBe(88);
    expect(s.spyValue).toBe(525);
    expect(s.hasLiveVix).toBe(false);
  });
});

describe("RegimePanel live COR1M crash trigger", () => {
  it("updates the crash-trigger decision from the active live COR1M value", () => {
    expect(resolveCrashTriggerState({
      liveCorrelation: true,
      correlation: 72,
      cachedCorrelationMet: false,
      spxBelowMa: true,
      realizedVolMet: true,
    })).toEqual({ correlationMet: true, triggered: true });
  });
});

describe("RegimePanel — VIX/VVIX timestamps refresh from latest WS values", () => {
  it("vixLastTs effect tracks last live VIX value", () => {
    const vixEffect = panelSource.match(
      /vixLastTs[\s\S]*?setVixLastTs[\s\S]*?(?=\}\s*,?\s*\[)/
    )?.[0] ?? "";
    expect(vixEffect).toContain("liveVix");
    expect(vixEffect).toContain("toLocaleTimeString()");
  });

  it("vvixLastTs effect tracks last live VVIX value", () => {
    expect(panelSource).toContain("setVvixLastTs");
    expect(panelSource).toContain("liveVvix");
    expect(panelSource).toContain("toLocaleTimeString()");
  });
});

describe("RegimePanel — liveCri should recompute when live symbols stream in", () => {
  it("liveCri useMemo returns null when effectiveHasLive is false (market closed or no WS data)", () => {
    const criMemo = panelSource.match(
      /liveCri[\s\S]*?computeCri[\s\S]*?(?=\}\s*,?\s*\[)/
    )?.[0] ?? "";
    expect(criMemo).toContain("if (!effectiveHasLive)");
  });
});
