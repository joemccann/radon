/**
 * Mobile toast viewport must sit above the tab bar, not under the app bar.
 * Pinning to the top covers hero metrics (Portfolio Total account / balance).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..");
const css = readFileSync(join(WEB, "app/globals.css"), "utf8");
const viewport = readFileSync(join(WEB, "components/ToastViewport.tsx"), "utf8");

function ruleBlock(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  expect(start, `${selector} rule missing`).toBeGreaterThan(-1);
  const end = css.indexOf("}", start);
  return css.slice(start, end);
}

describe("toast mobile placement", () => {
  it("desktop viewport stays bottom-right", () => {
    const block = ruleBlock(".toast-container");
    expect(block).toContain("position: fixed");
    expect(block).toContain("bottom: 20px");
    expect(block).toContain("right: 20px");
    expect(block).not.toMatch(/^\s*top:/m);
    expect(block).not.toMatch(/^\s*left:/m);
  });

  it("mobile viewport sits above the tab bar like .toast-stack", () => {
    const stack = ruleBlock('body[data-mobile="true"] .toast-stack');
    expect(stack).toContain(
      "bottom: calc(1rem + var(--mobile-tab-bar-height) + var(--safe-bottom))",
    );

    const block = ruleBlock('body[data-mobile="true"] .toast-container');
    expect(block).toContain(
      "bottom: calc(1rem + var(--mobile-tab-bar-height) + var(--safe-bottom))",
    );
    expect(block).toMatch(/top:\s*auto/);
    expect(block).not.toMatch(/--mobile-app-bar-height/);
    expect(block).toMatch(/left:\s*12px/);
    expect(block).toMatch(/right:\s*12px/);
    expect(block).toContain("--toast-viewport-max-height: 40dvh");
  });

  it("mobile toasts stretch across the padded viewport without stealing taps", () => {
    const toast = ruleBlock('body[data-mobile="true"] .toast');
    expect(toast).toMatch(/max-width:\s*none/);
    expect(toast).toMatch(/pointer-events:\s*none/);
    const actions = ruleBlock('body[data-mobile="true"] .toast button');
    expect(actions).toMatch(/pointer-events:\s*auto/);
  });

  it("viewport max-width is a CSS variable so mobile can go full-bleed", () => {
    expect(viewport).toContain(
      'maxWidth = "var(--toast-viewport-max-width, calc(100vw - 40px))"',
    );
    const block = ruleBlock('body[data-mobile="true"] .toast-container');
    expect(block).toContain("--toast-viewport-max-width: none");
  });

  it("success/error/warning accents stay on the toast, not the viewport", () => {
    expect(ruleBlock(".toast-success")).toContain("border-left: 3px solid var(--positive)");
    expect(ruleBlock(".toast-error")).toContain("border-left: 3px solid var(--negative)");
    expect(ruleBlock(".toast-warning")).toContain("border-left: 3px solid var(--warning)");
  });
});
