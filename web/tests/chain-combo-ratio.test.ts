import { describe, expect, it } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";

import { normalizeComboOrder } from "../lib/optionsChainUtils";

const SRC = fs.readFileSync(
  path.resolve(__dirname, "../components/ticker-detail/OptionsChainTab.tsx"),
  "utf-8",
);

describe("Combo order sizing", () => {
  it("derives combo quantity plus per-leg ratios from entered leg sizes", () => {
    const normalized = normalizeComboOrder([
      {
        id: "AAOI_20260417_85_P",
        action: "SELL",
        right: "P",
        strike: 85,
        expiry: "20260417",
        quantity: 25,
        limitPrice: 9.05,
      },
      {
        id: "AAOI_20260417_90_C",
        action: "BUY",
        right: "C",
        strike: 90,
        expiry: "20260417",
        quantity: 50,
        limitPrice: 4.2,
      },
    ]);

    expect(normalized.quantity).toBe(25);
    expect(normalized.legs.map((leg) => leg.quantity)).toEqual([1, 2]);
  });

  it("builds combo payload from normalized ratios instead of hardcoding 1x1", () => {
    expect(SRC).toContain("const normalizedOrder = useMemo(() => (isCombo ? normalizeComboOrder(legs) : null), [isCombo, legs]);");
    expect(SRC).toContain("ratio: l.quantity,");
    expect(SRC).not.toContain("ratio: 1,");
  });
});
