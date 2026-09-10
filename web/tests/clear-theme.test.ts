import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");
const presentation = readFileSync(resolve(__dirname, "../app/clear.css"), "utf8");
const layout = readFileSync(resolve(__dirname, "../app/layout.tsx"), "utf8");

function palette(theme: string): Record<string, string> {
  const body = css.match(new RegExp(`\\[data-theme="${theme}"\\]\\s*\\{([\\s\\S]*?)\\n\\}`))?.[1];
  expect(body).toBeDefined();
  const tokens = Object.fromEntries([...body!.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/(--[a-z-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
  const resolveValue = (value: string): string => value.startsWith("var(") && tokens[value.slice(4, -1)] ? resolveValue(tokens[value.slice(4, -1)]) : value;
  return Object.fromEntries(Object.entries(tokens).map(([key, value]) => [key, value.startsWith("var(") ? resolveValue(value) : value]));
}

function luminance(hex: string): number {
  const rgb = hex.replace("#", "").match(/../g)!.map((part) => parseInt(part, 16) / 255)
    .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
}

function contrast(a: string, b: string): number {
  const [bright, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (bright + 0.05) / (dark + 0.05);
}

describe("Clear theme", () => {
  it("uses the selected light reference and one global presentation layer", () => {
    expect(palette("light")).toMatchObject({
      "--bg-canvas": "#ffffff", "--bg-subtle": "#f6f8f8", "--text-primary": "#172624",
      "--text-muted": "#62716d", "--signal-core": "#087f53", "--line-grid": "#e4eae7",
    });
    expect(layout).toContain('import "./clear.css"');
    expect(layout).toContain('className="app-root radon-clear"');
    expect(presentation).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
    expect(presentation).not.toMatch(/transition:\s*all|backdrop-filter/);
  });

  for (const theme of ["light", "dark"]) {
    it(`${theme}: financial text and status colors meet AA on both reading surfaces`, () => {
      const p = palette(theme);
      for (const foreground of ["--text-primary", "--text-secondary", "--text-muted", "--signal-core-text", "--warn-text", "--negative"]) {
        for (const background of ["--bg-canvas", "--bg-subtle"]) {
          expect(contrast(p[foreground], p[background]), `${theme} ${foreground} on ${background}`).toBeGreaterThanOrEqual(4.5);
        }
      }
      expect(contrast(p["--text-on-accent"], p["--signal-core"])).toBeGreaterThanOrEqual(4.5);
      expect(p["--chart-surface"]).toBe(p["--bg-panel"]);
    });
  }
});
