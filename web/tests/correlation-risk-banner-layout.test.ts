import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function readSource(relPath: string): Promise<string> {
  return readFile(path.resolve(__dirname, relPath), "utf8");
}

function cssBlock(source: string, selector: string): string {
  const match = source.match(new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([\\s\\S]*?)\\}`));
  return match?.[1] ?? "";
}

describe("CorrelationRiskBanner layout CSS", () => {
  it("keeps the title and GATE 3 pill on one header row", async () => {
    const css = await readSource("../app/globals.css");
    const header = cssBlock(css, ".crb-header");
    const title = cssBlock(css, ".crb-title");

    expect(header).toContain("display: flex");
    expect(header).toContain("align-items: center");
    expect(header).toContain("justify-content: space-between");
    expect(header).toContain("flex-wrap: nowrap");
    expect(title).toContain("display: flex");
    expect(title).toContain("align-items: center");
    expect(title).toContain("min-width: 0");
  });

  it("renders insufficient-history names as wrapping chips", async () => {
    const css = await readSource("../app/globals.css");
    const tickers = cssBlock(css, ".crb-tickers");
    const chip = cssBlock(css, ".crb-ticker");

    expect(tickers).toContain("display: flex");
    expect(tickers).toContain("flex-wrap: wrap");
    expect(chip).toContain("font-family: var(--font-mono)");
    expect(chip).not.toMatch(/border-radius:\s*(?:[5-9]|\d{2,})px/);
  });

  it("gives the unmeasured level its own non-calm accent token", async () => {
    const css = await readSource("../app/globals.css");
    const unmeasured = cssBlock(css, '.crb[data-level="unmeasured"]');

    expect(unmeasured).toContain("--crb-accent");
    expect(unmeasured).toContain("var(--warn)");
    expect(unmeasured).not.toContain("var(--positive)");
    expect(unmeasured).not.toMatch(/#[0-9a-fA-F]{3,8}/);
  });

  it("stays a compact instrument module with a 4px outer radius", async () => {
    const css = await readSource("../app/globals.css");
    const module = cssBlock(css, ".crb");

    expect(module).toContain("border-radius: 4px");
    expect(module).toContain("padding:");
    expect(module).toContain("border-left:");
    expect(module).toContain("--crb-accent");
    expect(module).not.toContain("transition: all");
  });
});
