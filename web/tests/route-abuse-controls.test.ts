import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  boundedPositiveInt,
  boundedTicker,
  boundedUniqueTickers,
  OPTION_EXPIRY_PATTERN,
} from "@/lib/requestBounds";

describe("provider and subprocess input budgets", () => {
  it("accepts only bounded ticker syntax", () => {
    expect(boundedTicker(" brk.b ")).toBe("BRK.B");
    expect(boundedTicker("../../etc/passwd")).toBeNull();
    expect(boundedTicker("A".repeat(11))).toBeNull();
  });

  it("deduplicates ticker batches and rejects over-cardinality input", () => {
    expect(boundedUniqueTickers(["aapl", "AAPL", "msft"], 30)).toEqual(["AAPL", "MSFT"]);
    expect(boundedUniqueTickers(Array.from({ length: 31 }, (_, index) => `A${index}`), 30)).toBeNull();
    expect(boundedUniqueTickers(["AAPL", {}], 30)).toBeNull();
  });

  it("bounds numeric query fan-out and option expiry syntax", () => {
    expect(boundedPositiveInt("50", 20, 50)).toBe(50);
    expect(boundedPositiveInt("51", 20, 50)).toBeNull();
    expect(boundedPositiveInt("2x", 20, 50)).toBeNull();
    expect(OPTION_EXPIRY_PATTERN.test("20260821")).toBe(true);
    expect(OPTION_EXPIRY_PATTERN.test("2026-08-21")).toBe(true);
    expect(OPTION_EXPIRY_PATTERN.test("../../etc")).toBe(false);
  });
});

describe("dashboard request ownership", () => {
  it("keeps portfolio polling in the workspace shell instead of remounting it in the palette", () => {
    const components = fileURLToPath(new URL("../components/", import.meta.url));
    const palette = readFileSync(`${components}CommandPalette.tsx`, "utf8");
    const shell = readFileSync(`${components}WorkspaceShell.tsx`, "utf8");

    expect(palette).not.toContain('from "@/lib/usePortfolio"');
    expect(palette).not.toContain("usePortfolio(");
    expect(shell).toContain("<CommandPalette");
    expect(shell).toContain("portfolioSymbols={portfolioSymbols}");
  });
});
