import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");

function declarationBlock(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  expect(match, `missing CSS block for ${selector}`).not.toBeNull();
  return match?.[1] ?? "";
}

describe("typography foundation", () => {
  it("defines semantic type roles and disables synthetic font faces", () => {
    const root = declarationBlock(":root");
    expect(root).toContain("--text-meta: 12px");
    expect(root).toContain("--text-label: 13px");
    expect(root).toContain("--text-table: 13px");
    expect(root).toContain("--text-prose: 14px");
    expect(root).toContain("--text-panel-title: 18px");
    expect(root).toContain("--text-view-title: 28px");
    expect(root).toContain("--text-metric: 28px");

    const html = declarationBlock("html");
    expect(html).toContain("font-synthesis: none");
    expect(html).toContain("-webkit-font-smoothing: antialiased");
    expect(html).toContain("-moz-osx-font-smoothing: grayscale");
  });

  it("uses an AA-compliant light-theme muted text token", () => {
    expect(declarationBlock('[data-theme="light"]')).toContain("--text-muted: #62716d");
  });

  it("uses Inter for structural titles and stable primary metrics", () => {
    for (const selector of [".section-title", ".modal-title"]) {
      const block = declarationBlock(selector);
      expect(block).toContain("font-family: var(--font-sans)");
      expect(block).toContain("font-size: var(--text-label)");
      expect(block).toContain("font-weight: 600");
      expect(block).toContain("letter-spacing: var(--tracking-label)");
    }

    const metric = declarationBlock(".metric-value");
    expect(metric).toContain("font-family: var(--font-sans)");
    expect(metric).toContain("font-size: var(--text-metric)");
    expect(metric).toContain("font-variant-numeric: tabular-nums");
  });

  it("keeps monospaced emphasis on loaded weights", () => {
    for (const selector of [
      ".theta-card__ticker",
      ".strength-card__score span",
      ".grg-score",
      ".grg-metric-value",
      ".grg-asset-ticker",
      ".grg-asset-main",
      ".grg-gate-status",
    ]) {
      expect(declarationBlock(selector)).toContain("font-weight: 700");
    }
  });
});
