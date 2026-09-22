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

describe("ModifyOrderModal layout CSS", () => {
  it("contains focus styling on the input row instead of the inner input", async () => {
    const css = await readSource("../app/globals.css");
    const rowFocus = cssBlock(css, ".modify-price-input-row:focus-within");
    const inputFocus = cssBlock(css, ".modify-price-input:focus");

    expect(rowFocus).toContain("border-color: var(--border-focus)");
    expect(inputFocus).not.toContain("inset 0 0 0 1px");
  });

  it("keeps combo price fields responsive inside the left pricing panel", async () => {
    const css = await readSource("../app/globals.css");
    const comboGrid = cssBlock(css, ".modify-field-grid-combo");
    const inputRow = cssBlock(css, ".modify-price-input-row");
    const input = cssBlock(css, ".modify-price-input");

    expect(comboGrid).toContain("repeat(auto-fit");
    expect(comboGrid).toContain("minmax(min(100%, 120px), 1fr)");
    expect(inputRow).toContain("min-width: 0");
    expect(input).toContain("min-width: 0");
  });

  it("lets reference-price buttons shrink and wrap instead of overflowing the panel", async () => {
    const css = await readSource("../app/globals.css");
    const clear = await readSource("../app/clear.css");
    const row = cssBlock(css, ".modify-quick-buttons");
    const button = cssBlock(css, ".btn-quick");
    const clearButton = cssBlock(clear, ".radon-clear .btn-quick");

    expect(row).toContain("flex-wrap: wrap");
    expect(button).toContain("min-width: 0");
    expect(clearButton).toContain("padding-inline: 8px");
  });
});
