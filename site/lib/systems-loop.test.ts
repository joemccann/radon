import { describe, expect, it } from "vitest";
import { SYSTEM_LOOP_CASES } from "./systems-loop";

const EM_OR_EN_DASH = /[–—]/;

describe("systems loop ticket contract", () => {
  it("ships four view sources with a complete expression ticket", () => {
    expect(SYSTEM_LOOP_CASES.map((entry) => entry.key)).toEqual([
      "flow",
      "regime",
      "news",
      "scanner",
    ]);
    for (const entry of SYSTEM_LOOP_CASES) {
      expect(entry.expiry.length).toBeGreaterThan(0);
      expect(entry.strikes.length).toBeGreaterThan(0);
      expect(entry.debit.length).toBeGreaterThan(0);
      expect(entry.maxLoss.length).toBeGreaterThan(0);
      expect(entry.size).toMatch(/% bankroll$/);
      expect(`${entry.label} ${entry.reading} ${entry.structure}`).not.toMatch(
        EM_OR_EN_DASH,
      );
    }
  });

  it("does not invent a futures overlay for an options structure", () => {
    const scanner = SYSTEM_LOOP_CASES.find((entry) => entry.key === "scanner");
    expect(scanner).toBeDefined();
    expect(scanner!.vehicle).toBe("Options");
    expect(scanner!.structure.toLowerCase()).not.toMatch(/futures/);
    expect(scanner!.structure.toLowerCase()).toContain("strangle");
  });

  it("keeps stock linear instead of a fake options ratio", () => {
    const news = SYSTEM_LOOP_CASES.find((entry) => entry.key === "news");
    expect(news).toBeDefined();
    expect(news!.vehicle).toBe("Common stock");
    expect(news!.convexity).toBe("Linear");
    expect(news!.expiry).toBe("Spot");
  });
});
