import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));
const PANEL_PATH = join(TEST_DIR, "../components/RegimePanel.tsx");
const CSS_PATH = join(TEST_DIR, "../app/globals.css");
const panelSource = readFileSync(PANEL_PATH, "utf-8");
const cssSource = readFileSync(CSS_PATH, "utf-8");

describe("RegimePanel — responsive history chart layout", () => {
  it("uses a named history-grid class instead of an inline fixed two-column grid", () => {
    expect(panelSource).toContain('className="regime-history-grid"');
    expect(panelSource).toContain('data-testid="regime-history-grid"');
    expect(panelSource).not.toContain('<div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>'); 
  });

  it("keeps the history grid as a single full-width column at every breakpoint", () => {
    const historyGridRule = cssSource.match(/\.regime-history-grid\s*\{([^}]+)\}/)?.[1] ?? "";
    expect(historyGridRule).toMatch(/grid-template-columns:\s*1fr/);
    expect(historyGridRule).not.toMatch(/repeat\(2/);
  });
});
