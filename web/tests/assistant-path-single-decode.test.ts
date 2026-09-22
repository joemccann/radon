import { describe, expect, it } from "vitest";

import { normalizePath } from "@/lib/assistant/catalog";
import { backendQueryPath } from "@/lib/assistant/backend";
import { compilePath } from "@/lib/assistant/catalogBuild";

// Percent-encode a string n times.
function enc(value: string, rounds: number): string {
  let out = value;
  for (let i = 0; i < rounds; i += 1) out = encodeURIComponent(out);
  return out;
}

describe("assistant path normalization decodes exactly once, fail-closed", () => {
  it("rejects a path whose slot still decodes after the decode budget", () => {
    const nested = `/options/rv-ratio/${enc("/orders/place", 5)}/scan`;
    expect(normalizePath(nested)).toBe("");
  });

  it("still accepts ordinary and singly-encoded paths", () => {
    expect(normalizePath("/portfolio")).toBe("/portfolio");
    expect(normalizePath("/options/rv-ratio/SPY/scan")).toBe("/options/rv-ratio/SPY/scan");
  });

  it("backendQueryPath forwards an authorized path verbatim without re-normalizing", () => {
    expect(backendQueryPath("/portfolio")).toBe("/portfolio");
    expect(backendQueryPath("/portfolio", { a: "1" })).toBe("/portfolio?a=1");
  });

  it("backendQueryPath refuses a path that is not already a normalization fixpoint", () => {
    expect(() => backendQueryPath("/x/%2E%2E/orders")).toThrow();
    expect(() => backendQueryPath("/x/../orders")).toThrow();
  });

  it("generic slots admit only safe token characters", () => {
    const pattern = compilePath("/options/rv-ratio/{symbol}/scan");
    expect(pattern.test("/options/rv-ratio/SPY/scan")).toBe(true);
    expect(pattern.test("/options/rv-ratio/BRK.B/scan")).toBe(true);
    expect(pattern.test("/options/rv-ratio/a%2Fb/scan")).toBe(false);
    expect(pattern.test("/options/rv-ratio/a?b/scan")).toBe(false);
    expect(pattern.test("/options/rv-ratio/a#b/scan")).toBe(false);
  });
});
