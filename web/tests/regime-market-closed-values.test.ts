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

describe("RegimePanel — VIX/VVIX/SPY values prefer live WS data when present", () => {
  it("vixVal uses live websocket values when available", () => {
    expect(helperSource).toContain("const liveVix = prices.VIX?.last ?? null;");
    expect(helperSource).toContain("const vixValue = liveVix ?? data?.vix ?? null;");
  });

  it("vvixVal uses live websocket values when available", () => {
    expect(helperSource).toContain("const liveVvix = prices.VVIX?.last ?? null;");
    expect(helperSource).toContain("const vvixValue = liveVvix ?? data?.vvix ?? null;");
  });

  it("spyVal uses live websocket values when available", () => {
    expect(helperSource).toContain("const liveSpy = prices.SPY?.last ?? null;");
    expect(helperSource).toContain("const spyValue = liveSpy ?? data?.spy ?? null;");
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
    const vvixEffect = panelSource.match(
      /vvixLastTs[\s\S]*?setVvixLastTs[\s\S]*?(?=\}\s*,?\s*\[)/
    )?.[0] ?? "";
    expect(vvixEffect).toContain("liveVvix");
    expect(vvixEffect).toContain("toLocaleTimeString()");
  });
});

describe("RegimePanel — liveCri should recompute when live symbols stream in", () => {
  it("liveCri useMemo returns null only when no live values are present", () => {
    const criMemo = panelSource.match(
      /liveCri[\s\S]*?computeCri[\s\S]*?(?=\}\s*,?\s*\[)/
    )?.[0] ?? "";
    expect(criMemo).toContain("if (!hasLive)");
  });
});
