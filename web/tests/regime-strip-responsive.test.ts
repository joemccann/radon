import { describe, expect, it } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));
const GLOBALS_PATH = join(TEST_DIR, "../app/globals.css");
const PANEL_PATH = join(TEST_DIR, "../components/RegimePanel.tsx");
const STRIP_PATH = join(TEST_DIR, "../components/RegimeStrip.tsx");
const cssSource = readFileSync(GLOBALS_PATH, "utf-8");
const panelSource = readFileSync(PANEL_PATH, "utf-8");
const stripSource = readFileSync(STRIP_PATH, "utf-8");

function extractBlock(source: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return source.match(new RegExp(`${escaped}\\s*\\{[\\s\\S]*?\\n\\}`, "m"))?.[0] ?? "";
}

describe("Regime strip responsive layout", () => {
  it("uses a tiered grid: five columns by default, a symmetric 3x2 presentation at narrower desktop widths, then a single-column stack on smaller screens", () => {
    const stripBlock = extractBlock(cssSource, ".regime-strip");

    expect(stripBlock).toContain("grid-template-columns");
    expect(stripBlock).toContain("repeat(5, minmax(0, 1fr))");
    expect(cssSource).toContain("@media (max-width: 1180px)");
    expect(cssSource).toContain("grid-template-columns: repeat(6, minmax(0, 1fr))");
    expect(cssSource).toContain(".regime-strip-cell:nth-child(-n + 3)");
    expect(cssSource).toContain(".regime-strip-cell:nth-child(n + 4)");
    expect(cssSource).toContain("grid-column: span 2");
    expect(cssSource).toContain("grid-column: span 3");
    expect(cssSource).toContain("@media (max-width: 760px)");
    expect(cssSource).toContain("grid-template-columns: 1fr");
  });

  it("swaps the strip label from REALIZED VOL to RVOL in the compressed strip state", () => {
    expect(panelSource).toContain("REALIZED VOL");
    expect(panelSource).toContain("RVOL");
    expect(cssSource).toContain("@media (max-width: 1180px)");
    expect(cssSource).toContain(".regime-strip-label-text-short");
    expect(cssSource).toContain(".regime-strip-label-text-full");
  });

  it("uses an inline left-aligned delta row when the strip stacks vertically on small screens", () => {
    expect(cssSource).toContain("@media (max-width: 760px)");
    expect(cssSource).toContain(".regime-strip-day-chg");
    expect(cssSource).toContain("display: flex");
    expect(cssSource).toContain("justify-content: flex-start");
    expect(cssSource).toContain("white-space: nowrap");
  });

  it("uses a two-column telemetry-rail layout when the strip stacks vertically on small screens", () => {
    expect(cssSource).toContain("@media (max-width: 760px)");
    expect(cssSource).toContain(".regime-strip-cell");
    expect(cssSource).toContain("grid-template-columns: minmax(96px, 112px) minmax(0, 1fr)");
    expect(cssSource).toContain(".regime-strip-primary");
    expect(cssSource).toContain(".regime-strip-meta-row");
    expect(cssSource).toContain("border-left: 1px solid");
    expect(cssSource).toContain("padding-left: 12px");
    expect(cssSource).toContain("justify-content: flex-start");
    expect(cssSource).toContain("font-size: 16px");
    expect(stripSource).toContain("regime-strip-primary");
    expect(stripSource).toContain("regime-strip-meta-row");
  });
});

describe("Regime strip small-screen value overflow", () => {
  it("lets a long value wrap inside the rail instead of painting over the meta column", () => {
    const mobileSource = cssSource.slice(cssSource.indexOf(".regime-strip-cell:nth-child(n)"));
    const valueBlock =
      mobileSource.match(/\.regime-strip-value\s*\{[\s\S]*?\}/m)?.[0] ?? "";
    expect(valueBlock).not.toContain("white-space: nowrap");
    expect(valueBlock).toContain("overflow-wrap: anywhere");
  });
});
