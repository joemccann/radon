import { describe, expect, it } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));
const GLOBALS_PATH = join(TEST_DIR, "../app/globals.css");
const PANEL_PATH = join(TEST_DIR, "../components/RegimePanel.tsx");
const cssSource = readFileSync(GLOBALS_PATH, "utf-8");
const panelSource = readFileSync(PANEL_PATH, "utf-8");

describe("Regime detail panels responsive layout", () => {
  it("uses a shared detail-grid class instead of an inline permanent two-column split", () => {
    expect(panelSource).toContain('className="regime-detail-grid"');
    expect(panelSource).not.toContain('gridTemplateColumns: "1fr 1fr"');
  });

  it("stacks the detail panels at narrower widths through CSS", () => {
    expect(cssSource).toContain(".regime-detail-grid");
    expect(cssSource).toContain("grid-template-columns: repeat(2, minmax(0, 1fr))");
    // 1240px, not 980px: the grouped rail takes 260px from the detail pane.
    expect(cssSource).toContain("@media (max-width: 1240px)");
    expect(cssSource).toContain("grid-template-columns: 1fr");
  });
});
