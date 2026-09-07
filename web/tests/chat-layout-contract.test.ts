import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");

function ruleBlock(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, `${selector} rule missing`).toBeGreaterThan(-1);
  return css.slice(start, css.indexOf("}", start));
}

describe("Radon Chat layout", () => {
  it("lets short conversations size to content instead of stretching to the viewport", () => {
    expect(ruleBlock(".chat-launcher__panel")).toMatch(/align-self:\s*flex-start/);
  });
});
