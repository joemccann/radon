import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../app/clear.css"), "utf8");

function ruleBlock(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, `${selector} rule missing`).toBeGreaterThan(-1);
  return css.slice(start, css.indexOf("}", start));
}

describe("Radon Chat layout", () => {
  it("keeps the conversation workspace bounded to the viewport", () => {
    expect(ruleBlock(".chat-launcher__panel:has(.chat-panel[data-empty])")).toMatch(/height:\s*min\(780px, calc\(100dvh - 64px\)\)/);
  });
});
