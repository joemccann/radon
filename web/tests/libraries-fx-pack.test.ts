/**
 * @vitest-environment node
 *
 * libraries.dev pack C — brand-safe contracts for thinking-orbs + border-beam.
 * Beams stay off when a gate is idle, cleared, or failed. Orb verbs map to
 * existing wait states only. Forbidden packages stay out of web/package.json.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  FOUR_GATES,
  gateBeamActive,
  ibStatusBeamActive,
  thinkingOrbState,
} from "../lib/librariesFx";

describe("thinking-orbs wait mapping", () => {
  it("maps existing async waits onto distinct orb verbs", () => {
    expect(thinkingOrbState("flow")).toBe("searching");
    expect(thinkingOrbState("gex")).toBe("weaving");
    expect(thinkingOrbState("evaluate")).toBe("solving");
    expect(thinkingOrbState("agent")).toBe("working");
    expect(thinkingOrbState("compute")).toBe("composing");
  });
});

describe("Gate 01-04 beam contract", () => {
  it("exposes the four sequential gates", () => {
    expect(FOUR_GATES.map((gate) => gate.id)).toEqual(["01", "02", "03", "04"]);
  });

  it("beams only while a gate is evaluating", () => {
    expect(gateBeamActive("evaluating")).toBe(true);
    expect(gateBeamActive("idle")).toBe(false);
    expect(gateBeamActive("cleared")).toBe(false);
    expect(gateBeamActive("failed")).toBe(false);
  });
});

describe("IB status beam", () => {
  it("beams only the live connected control", () => {
    expect(ibStatusBeamActive("connected")).toBe(true);
    expect(ibStatusBeamActive("demo")).toBe(false);
    expect(ibStatusBeamActive("relay_offline")).toBe(false);
    expect(ibStatusBeamActive("awaiting_2fa")).toBe(false);
  });
});

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
