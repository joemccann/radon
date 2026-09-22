import { describe, expect, it } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";

const CHAIN_SRC = fs.readFileSync(
  path.resolve(__dirname, "../components/ticker-detail/OptionsChainTab.tsx"),
  "utf-8",
);
const CSS_SRC = fs.readFileSync(
  path.resolve(__dirname, "../app/globals.css"),
  "utf-8",
);

describe("OptionsChainTab side filter", () => {
  it("has sideFilter state seeded from the URL (default 'both')", () => {
    // The side filter is now deep-linked: state seeds from useChainUrlState
    // (which defaults to "both" when the ?side param is absent/invalid).
    expect(CHAIN_SRC).toContain("useState<SideFilter>(() => chainUrl.initialSide)");
    expect(CHAIN_SRC).toContain("useChainUrlState");
  });

  it("renders ALL / CALLS / PUTS toggle buttons", () => {
    expect(CHAIN_SRC).toContain("chain-side-toggle");
    expect(CHAIN_SRC).toContain("chain-side-toggle-btn");
    expect(CHAIN_SRC).toContain('"ALL"');
  });

  it("passes sideFilter prop to StrikeRow", () => {
    expect(CHAIN_SRC).toContain("sideFilter={sideFilter}");
  });

  it("StrikeRow accepts sideFilter prop", () => {
    expect(CHAIN_SRC).toContain('sideFilter: "both" | "calls" | "puts"');
  });

  it("conditionally renders call columns based on sideFilter", () => {
    expect(CHAIN_SRC).toContain('sideFilter !== "puts"');
  });

  it("conditionally renders put columns based on sideFilter", () => {
    expect(CHAIN_SRC).toContain('sideFilter !== "calls"');
  });

  it("headers also respect the filter", () => {
    // Both header rows should conditionally render call/put headers
    const headerMatches = CHAIN_SRC.match(/sideFilter !== "puts"/g);
    const putMatches = CHAIN_SRC.match(/sideFilter !== "calls"/g);
    // At least 2 each: header row + side-label row (plus StrikeRow)
    expect(headerMatches!.length).toBeGreaterThanOrEqual(3);
    expect(putMatches!.length).toBeGreaterThanOrEqual(3);
  });
});

describe("Chain sticky headers (CSS)", () => {
  it("chain-grid uses border-separate (not collapse, which breaks sticky)", () => {
    // Extract .chain-grid rule
    const match = CSS_SRC.match(/\.chain-grid\s*\{[^}]*\}/);
    expect(match).not.toBeNull();
    expect(match![0]).toContain("border-collapse: separate");
    expect(match![0]).not.toContain("border-collapse: collapse");
  });

  it("chain-header has position: sticky", () => {
    const match = CSS_SRC.match(/\.chain-header\s*\{[^}]*position:\s*sticky/);
    expect(match).not.toBeNull();
  });

  it("chain-header has top: 0", () => {
    const match = CSS_SRC.match(/\.chain-header\s*\{[^}]*top:\s*0/);
    expect(match).not.toBeNull();
  });

  it("chain-side-label has position: sticky", () => {
    const match = CSS_SRC.match(/\.chain-side-label\s*\{[^}]*position:\s*sticky/);
    expect(match).not.toBeNull();
  });

  it("each chain pane has overflow-y: auto for its own scroll context", () => {
    const match = CSS_SRC.match(/\.chain-anchor-pane\s*\{[^}]*overflow-y:\s*auto/);
    expect(match).not.toBeNull();
  });
});

describe("Chain side toggle CSS", () => {
  it("has chain-side-toggle styles", () => {
    expect(CSS_SRC).toContain(".chain-side-toggle");
    expect(CSS_SRC).toContain(".chain-side-toggle-btn");
  });

  it("toggle uses mono font", () => {
    expect(CSS_SRC).toMatch(/\.chain-side-toggle-btn\s*\{[^}]*var\(--font-mono\)/);
  });

  it("active state has distinct styling", () => {
    expect(CSS_SRC).toContain(".chain-side-toggle-btn.active");
  });
});
