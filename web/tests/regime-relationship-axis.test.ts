import { describe, expect, it } from "vitest";
import {
  buildTickIndices,
  resolveRelationshipTickCount,
} from "../components/RegimeRelationshipView";

describe("RegimeRelationshipView x-axis density", () => {
  it("uses more than four date ticks when the chart has wide horizontal room", () => {
    expect(resolveRelationshipTickCount(696)).toBeGreaterThan(4);
  });

  it("caps the number of ticks so labels stay legible", () => {
    expect(resolveRelationshipTickCount(1200)).toBeLessThanOrEqual(7);
    expect(resolveRelationshipTickCount(320)).toBeGreaterThanOrEqual(4);
  });

  it("builds first/last inclusive tick indices for the requested density", () => {
    const ticks = buildTickIndices(20, 6);
    expect(ticks[0]).toBe(0);
    expect(ticks.at(-1)).toBe(19);
    expect(ticks.length).toBeGreaterThanOrEqual(6);
  });
});
