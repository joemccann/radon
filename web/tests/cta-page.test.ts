/**
 * Unit tests: /cta page structure — source inspection
 *
 * Verifies the CTA page has:
 *  1. Vol-targeting model rendered above the CTA tables
 *  2. SortableCtaTable used (not the old CtaTables)
 *  3. CTA section removed from RegimePanel
 *  4. /cta route exists in nav items
 *  5. WorkspaceSection type includes "cta"
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const ROOT = join(__dirname, "..");

function read(rel: string) {
  return readFileSync(join(ROOT, rel), "utf-8");
}

describe("lib/data.ts — cta nav item", () => {
  const data = read("lib/data.ts");

  it("navItems includes a cta entry with href /cta", () => {
    expect(data).toMatch(/href.*\/cta/);
  });

  it("quickPromptsBySection includes cta section", () => {
    expect(data).toMatch(/cta:/);
  });

  it("sectionDescription includes cta section", () => {
    expect(data).toContain("cta:");
  });
});

describe("lib/types.ts — WorkspaceSection includes cta", () => {
  const types = read("lib/types.ts");

  it("WorkspaceSection union includes 'cta'", () => {
    expect(types).toMatch(/"cta"/);
  });
});

describe("components/CtaPage.tsx — structure", () => {
  const src = read("components/CtaPage.tsx");

  it("renders vol-targeting model", () => {
    expect(src).toMatch(/VOL-TARGETING|exposure_pct|forced_reduction/i);
  });

  it("shows model inputs stamp and day-change companion metric", () => {
    expect(src).toMatch(/cta-model-inputs/);
    expect(src).toMatch(/cta-day-change/);
    expect(src).toMatch(/Implied cut vs AUM/);
    expect(src).toMatch(/formatCtaModelInputsStamp|ctaVolTarget/);
  });

  it("renders SortableCtaTable JSX (not old CtaTables)", () => {
    expect(src).toMatch(/<SortableCtaTable/);
    expect(src).not.toMatch(/import CtaTables/);
  });

  it("vol-targeting model comes before CTA tables in render order", () => {
    // Use JSX tag to avoid matching the import line
    const volIdx = src.indexOf("VOL-TARGETING");
    const tableIdx = src.indexOf("<SortableCtaTable");
    expect(volIdx).toBeGreaterThan(-1);
    expect(tableIdx).toBeGreaterThan(-1);
    expect(volIdx).toBeLessThan(tableIdx);
  });

  it("uses useRegime for vol-targeting data", () => {
    expect(src).toMatch(/useRegime/);
  });

  it("uses useMenthorqCta for CTA table data", () => {
    expect(src).toMatch(/useMenthorqCta/);
  });
});

describe("components/SortableCtaTable.tsx — sortable table", () => {
  const src = read("components/SortableCtaTable.tsx");

  it("exports SortableCtaTable component", () => {
    expect(src).toMatch(/export.*SortableCtaTable|export default.*SortableCtaTable/);
  });

  it("implements column sort via the shared useSort hook + SortTh", () => {
    expect(src).toMatch(/from ['"]@\/lib\/useSort['"]/);
    expect(src).toMatch(/useSort</);
    expect(src).toMatch(/sortKey=/);
  });

  it("renders sortable column headers via SortTh", () => {
    expect(src).toMatch(/import SortTh from ['"]\.\/SortTh['"]/);
    expect(src).toMatch(/<SortTh/);
    expect(src).toMatch(/onToggle=\{toggle\}/);
  });

  it("sorts rows via the shared useSort hook output", () => {
    // useSort returns the re-ordered `sorted` array consumed by the render path
    expect(src).toMatch(/\bsorted\b/);
    expect(src).toMatch(/const \{ sorted/);
  });

  it("renders a mobile card list instead of the wide table on mobile", () => {
    expect(src).toContain("cta-mobile-list");
    expect(src).toContain("useViewport");
  });
});

describe("components/RegimePanel.tsx — CTA section removed", () => {
  const src = read("components/RegimePanel.tsx");

  it("no longer renders CTA Exposure Model section in RegimePanel", () => {
    expect(src).not.toMatch(/CTA EXPOSURE MODEL/);
  });

  it("no longer imports CtaTables in RegimePanel", () => {
    expect(src).not.toMatch(/import CtaTables/);
  });
});

describe("components/CriHistoryChart.tsx — D3 chart exists", () => {
  const src = read("components/CriHistoryChart.tsx");

  it("uses d3", () => {
    expect(src).toMatch(/from ['"]d3['"]/);
  });

  it("exports CriHistoryChart", () => {
    expect(src).toMatch(/export.*CriHistoryChart|export default.*CriHistoryChart/);
  });

  it("accepts history prop (CriHistoryEntry[])", () => {
    expect(src).toMatch(/history/);
  });

  it("renders an SVG element", () => {
    expect(src).toMatch(/<svg|svg>/);
  });
});

describe("components/RegimePanel.tsx — uses CriHistoryChart", () => {
  const src = read("components/RegimePanel.tsx");

  it("imports CriHistoryChart", () => {
    expect(src).toMatch(/import.*CriHistoryChart/);
  });

  it("renders CriHistoryChart instead of raw history table", () => {
    expect(src).toMatch(/<CriHistoryChart/);
  });
});
