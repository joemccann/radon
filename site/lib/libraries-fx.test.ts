/**
 * libraries.dev pack C — marketing CTA / marker / hero contracts.
 * Primary demo CTAs get a mono beam. Gate and method markers share one
 * silver metal accent. Forbidden packages stay out of site/package.json.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  CTA_BEAM,
  HERO_BEAM,
  MARKER_METAL,
} from "./librariesFx";

describe("marketing CTA beam", () => {
  it("uses a mono beam, not a rainbow idle palette", () => {
    expect(CTA_BEAM.colorVariant).toBe("mono");
    expect(CTA_BEAM.staticColors).toBe(true);
    expect(CTA_BEAM.size).toBe("sm");
    expect(CTA_BEAM.strength).toBeLessThan(0.7);
  });
});

describe("gate and method marker accent", () => {
  it("uses one light silver metal system", () => {
    expect(MARKER_METAL.preset).toBe("silver");
    expect(MARKER_METAL.strength).toBeLessThan(0.5);
    expect(MARKER_METAL.variant).toBe("button");
  });
});

describe("hero accent", () => {
  it("keeps a single calm mono beam for the flow plate", () => {
    expect(HERO_BEAM.colorVariant).toBe("mono");
    expect(HERO_BEAM.staticColors).toBe(true);
    expect(HERO_BEAM.size).toBe("md");
  });
});

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
