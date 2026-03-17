import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));
const PANEL_PATH = join(TEST_DIR, "../components/RegimePanel.tsx");
const HELPER_PATH = join(TEST_DIR, "../lib/regimeLiveStrip.ts");
const panelSource = readFileSync(PANEL_PATH, "utf-8");
const helperSource = readFileSync(HELPER_PATH, "utf-8");

describe("RegimePanel — live COR1M rendering", () => {
  it("prefers the live COR1M websocket price over cached CRI COR1M when available", () => {
    expect(helperSource).toContain("const liveCor1m = prices.COR1M?.last ?? null;");
    expect(helperSource).toContain("const cor1mValue = liveCor1m ?? data?.cor1m ?? null;");
    expect(panelSource).toContain("cor1mValue: activeCorr");
  });

  it("shows a live badge for COR1M when a live COR1M value is present", () => {
    expect(panelSource).toContain("COR1M <LiveBadge live={hasLiveCor1m} />");
    expect(panelSource).not.toContain("COR1M <LiveBadge live={false} />");
  });

  it("pushes the live COR1M value into the RVOL/COR1M history chart", () => {
    expect(panelSource).toMatch(/rvolCorrLive\.cor1m\s*=\s*liveCor1m/);
  });

  it("anchors the live COR1M day-change line to the prior CRI/Cboe close, not the IB close field", () => {
    expect(helperSource).toContain("cor1m_previous_close");
    expect(helperSource).not.toContain('prices.COR1M?.close');
    expect(panelSource).toMatch(/<DayChange last=\{liveCor1m\} close=\{cor1mPreviousClose\} \/>/);
  });

  it("moves the COR1M 5d change into the muted strip subline", () => {
    expect(panelSource).toContain('5d chg:');
    expect(panelSource).toContain('sub={<>{`5d chg:');
  });
});
