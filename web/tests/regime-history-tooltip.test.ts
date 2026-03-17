import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";
import { SECTION_TOOLTIPS } from "../lib/sectionTooltips";

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));
const PANEL_PATH = join(TEST_DIR, "../components/RegimePanel.tsx");
const panelSource = readFileSync(PANEL_PATH, "utf-8");

describe("RegimePanel — 20-session history header contract", () => {
  it("groups the title text and info icon together before any live badge", () => {
    const titleIndex = panelSource.indexOf('data-testid="regime-history-title"');
    const iconIndex = panelSource.indexOf('triggerTestId="regime-history-tooltip-trigger"');
    const badgeIndex = panelSource.indexOf('{hasLive &&');

    expect(titleIndex).toBeGreaterThan(-1);
    expect(iconIndex).toBeGreaterThan(titleIndex);
    expect(badgeIndex).toBeGreaterThan(iconIndex);
    expect(panelSource).toContain('data-testid="regime-history-title-text"');
    expect(panelSource).toContain('SECTION_TOOLTIPS["20-SESSION HISTORY"]');
  });
});

describe("Regime history tooltip copy", () => {
  it("uses a history key that matches the visible section title", () => {
    expect(SECTION_TOOLTIPS["20-SESSION HISTORY"]).toBeDefined();
    expect(SECTION_TOOLTIPS["10-DAY HISTORY"]).toBeUndefined();
  });

  it("describes the visible signals without implementation jargon", () => {
    const copy = SECTION_TOOLTIPS["20-SESSION HISTORY"];

    expect(copy).toMatch(/20 trading sessions/i);
    expect(copy).toMatch(/Left chart/i);
    expect(copy).toMatch(/Right chart/i);
    expect(copy).toMatch(/VIX/i);
    expect(copy).toMatch(/VVIX/i);
    expect(copy).toMatch(/realized vol/i);
    expect(copy).toMatch(/COR1M/i);
    expect(copy).not.toMatch(/\bD3\b/i);
    expect(copy).not.toMatch(/\bWS\b/i);
    expect(copy).not.toMatch(/websocket/i);
  });
});
