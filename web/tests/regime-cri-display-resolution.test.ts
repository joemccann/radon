/**
 * R-200 (panel half): a dead CRI feed must not resolve to a renderable reading.
 *
 * RegimePanel branched on `data?.cri ?` — always truthy, because the route
 * always shipped the object — and carried its own SECOND
 * `{score: 0, level: "LOW"}` default on the same line. So even with the route
 * fixed, the panel would still paint `0 /100` in `levelColor("LOW")` with four
 * ComponentBars at `0.0/25`, each coloured `var(--positive)` by the `score < 8`
 * test, at the exact moment the feed was dead.
 */

import { describe, it, expect } from "vitest";
import { resolveCriDisplay } from "../lib/regimeLiveStrip";

const READING = {
  score: 41,
  level: "ELEVATED",
  components: { vix: 12, vvix: 9, correlation: 11, momentum: 9 },
};

describe("resolveCriDisplay", () => {
  it("reports a dead feed as unavailable, not as a zero reading", () => {
    const resolved = resolveCriDisplay({ missing: true, cri: null }, null);
    expect(resolved.available).toBe(false);
    expect(resolved.cri).toBeNull();
  });

  it("reports a null cri as unavailable even without the missing flag", () => {
    // Defence in depth: an older cached payload, or any other writer, could
    // ship a null cri without the flag. Neither may become 0/LOW.
    const resolved = resolveCriDisplay({ cri: null }, null);
    expect(resolved.available).toBe(false);
    expect(resolved.cri).toBeNull();
  });

  it("prefers a live reading when the cached payload is missing", () => {
    // Live VIX/VVIX/SPY still compute a genuine CRI; a dead cache must not
    // blank a panel that has real numbers to draw.
    const resolved = resolveCriDisplay({ missing: true, cri: null }, READING);
    expect(resolved.available).toBe(true);
    expect(resolved.cri).toEqual(READING);
  });

  it("prefers the live reading over the cached one", () => {
    const resolved = resolveCriDisplay({ cri: { ...READING, score: 12 } }, READING);
    expect(resolved.cri?.score).toBe(41);
  });

  it("passes a real cached reading through unchanged", () => {
    const resolved = resolveCriDisplay({ cri: READING }, null);
    expect(resolved.available).toBe(true);
    expect(resolved.cri).toEqual(READING);
  });

  it("treats a genuine zero reading as available", () => {
    // The whole point: 0/LOW is a legal market reading and must still render
    // when it is real. Only the absence of data is unavailable.
    const real = { score: 0, level: "LOW", components: { vix: 0, vvix: 0, correlation: 0, momentum: 0 } };
    const resolved = resolveCriDisplay({ cri: real }, null);
    expect(resolved.available).toBe(true);
    expect(resolved.cri).toEqual(real);
  });

  it("handles a null payload", () => {
    expect(resolveCriDisplay(null, null).available).toBe(false);
  });
});
