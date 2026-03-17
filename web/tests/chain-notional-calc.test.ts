import { describe, expect, it } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = fs.readFileSync(
  path.resolve(__dirname, "../components/ticker-detail/OptionsChainTab.tsx"),
  "utf-8",
);

describe("Order builder notional calculation", () => {
  it("notional multiplies netPrice by 100 (options multiplier) only, not by totalQty again", () => {
    // computeNetPrice already incorporates leg.quantity, so notional = |netPrice| * 100
    // Keep this tolerant to pre/post refactors while preventing double-counting.
    expect(SRC).toMatch(/parsedPrice\s*\*\s*totalQty\s*\*\s*100/);
    expect(SRC).toContain(" notional");
    expect(SRC).not.toContain("Math.abs(netPrice) * 100 * totalQty");
  });

  it("computeNetPrice already includes quantity in its calculation", () => {
    const utilsSrc = fs.readFileSync(
      path.resolve(__dirname, "../lib/optionsChainUtils.ts"),
      "utf-8",
    );
    // net += sign * mid * leg.quantity — quantity is baked into netPrice
    expect(utilsSrc).toContain("sign * mid * leg.quantity");
  });
});
