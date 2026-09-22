/**
 * @vitest-environment node
 *
 * libraries.dev pack C — package pins for web/package.json.
 * The beam / orb contracts (gates beam only while evaluating, IB beams only
 * when connected, orb verbs map to existing waits) are asserted at the
 * consumer renders in ./libraries-fx-surfaces.test.tsx instead of restating
 * each predicate entry-for-entry next to its implementation (T-456).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("web package pin", () => {
  const pkg = JSON.parse(
    readFileSync(resolve(import.meta.dirname, "../package.json"), "utf8"),
  ) as { dependencies: Record<string, string> };

  it("installs thinking-orbs and border-beam", () => {
    expect(pkg.dependencies["thinking-orbs"]).toMatch(/^\^?0\./);
    expect(pkg.dependencies["border-beam"]).toMatch(/^\^?1\./);
  });

  it("does not install liquid-gooey or img-fx", () => {
    expect(pkg.dependencies["liquid-gooey"]).toBeUndefined();
    expect(pkg.dependencies["img-fx"]).toBeUndefined();
  });
});
