/**
 * libraries.dev pack C — package pins for site/package.json.
 * The CTA / hero / marker FX contracts (mono beam, silver metal, calm
 * strengths) are asserted where the constants are consumed — at the
 * component renders in ./libraries-fx-consumers.test.ts — instead of
 * restating the object literals next to themselves here (T-456).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("site package pin", () => {
  const pkg = JSON.parse(
    readFileSync(resolve(import.meta.dirname, "../package.json"), "utf8"),
  ) as { dependencies: Record<string, string> };

  it("installs border-beam and metal-fx", () => {
    expect(pkg.dependencies["border-beam"]).toMatch(/^\^?1\./);
    expect(pkg.dependencies["metal-fx"]).toMatch(/^\^?1\./);
  });

  it("does not install liquid-gooey, img-fx, or thinking-orbs", () => {
    expect(pkg.dependencies["liquid-gooey"]).toBeUndefined();
    expect(pkg.dependencies["img-fx"]).toBeUndefined();
    expect(pkg.dependencies["thinking-orbs"]).toBeUndefined();
  });
});
