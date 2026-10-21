import { describe, expect, it } from "vitest";
import { highLowLineZone } from "../lib/highLowLine";

describe("raw High-Low Line StochRSI zones", () => {
  it.each([
    [null, "unavailable"], [Number.NaN, "unavailable"], [Number.POSITIVE_INFINITY, "unavailable"],
    [-0.01, "unavailable"], [1.01, "unavailable"],
    [0, "oversold"], [0.2, "oversold"], [0.2000001, "neutral"],
    [0.7999999, "neutral"], [0.8, "overbought"], [1, "overbought"],
  ] as const)("classifies %s as %s", (value, expected) => {
    expect(highLowLineZone(value)).toBe(expected);
  });
});
