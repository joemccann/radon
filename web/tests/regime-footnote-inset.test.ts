/**
 * Risk-indicator captions share one inset. The VIX-COR base-rate note was
 * 9px and flush with the card stroke while the Clear section header sits at
 * 22px. Every regime source/method paragraph uses the same class so the
 * header inset and the 12px metadata floor apply once.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const web = resolve(__dirname, "..");
const clear = readFileSync(resolve(web, "app/clear.css"), "utf8");
const globals = readFileSync(resolve(web, "app/globals.css"), "utf8");

const PANELS = [
  "components/VixCorPanel.tsx",
  "components/CreditSpreadPanel.tsx",
  "components/Skew2dPanel.tsx",
  "components/PanicIndexPanel.tsx",
  "components/SkewPanel.tsx",
  "components/CorPanel.tsx",
  "components/equibles/EquiblesShortCrowdingPanel.tsx",
  "components/VixTsPanel.tsx",
  "components/IvSpreadPanel.tsx",
  "components/BpiPanel.tsx",
  "components/VolConePanel.tsx",
  "components/RsiOversoldPanel.tsx",
  "components/StraddlePanel.tsx",
  "components/HhLevPanel.tsx",
  "components/HyAdPanel.tsx",
  "components/IvRankPanel.tsx",
  "components/MaRatioPanel.tsx",
  "components/YieldCurvePanel.tsx",
  "components/DispersionPanel.tsx",
  "components/DivYieldPanel.tsx",
  "components/MarginDebtPanel.tsx",
  "components/equibles-cot/EquiblesCotPanel.tsx",
  "components/equibles-ats-venue-share/AtsVenueSharePanel.tsx",
];

function block(css: string, selector: string): string {
  const start = css.indexOf(selector);
  expect(start, selector).toBeGreaterThanOrEqual(0);
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  return css.slice(open + 1, close);
}

describe("regime footnote inset", () => {
  it("matches the Clear section-header inline inset and the metadata floor", () => {
    const header = block(clear, ".radon-clear .section-header {");
    expect(header).toContain("padding: 18px 22px");
    const inset = block(clear, ".radon-clear .section > .section-footnote {");
    expect(inset).toContain("padding: 16px 22px 22px");
    expect(inset).toContain("border-top: 1px solid var(--line-grid)");
    const shared = block(clear, ".radon-clear .section-footnote {");
    expect(shared).toContain("padding-inline: 22px");
    expect(shared).toContain("font-size: var(--text-meta)");
    expect(shared).toContain("line-height: 1.6");
    expect(globals).not.toMatch(/\.vixcor-base-rate-note\s*\{[^}]*font-size:\s*9px/);
  });

  it("uses the shared class on every regime caption of this shape", () => {
    for (const path of PANELS) {
      const src = readFileSync(resolve(web, path), "utf8");
      expect(src, path).toContain('className="section-footnote"');
    }
    const vixcor = readFileSync(resolve(web, "components/VixCorPanel.tsx"), "utf8");
    expect(vixcor).toContain('data-testid="vixcor-base-rate-note"');
    expect(vixcor).toContain('data-testid="vixcor-source-footnote"');
  });
});
