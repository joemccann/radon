/**
 * @vitest-environment node
 *
 * The TS registry mirror in lib/quant.ts must match the Python source of
 * truth (scripts/quant/strategies.py): same keys, same labels, same order.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { DEFAULT_QUANT_STRATEGY, QUANT_STRATEGIES, isQuantStrategyKey, quantLabel } from "@/lib/quant";

const PY_SOURCE = readFileSync(join(__dirname, "..", "..", "scripts", "quant", "strategies.py"), "utf-8");

function pythonRegistry(): Array<{ key: string; label: string }> {
  const out: Array<{ key: string; label: string }> = [];
  const re = /key="([a-z_]+)",\s*\n\s*label="([^"]+)",/g;
  for (const m of PY_SOURCE.matchAll(re)) out.push({ key: m[1], label: m[2] });
  return out;
}

describe("QUANT_STRATEGIES mirrors scripts/quant/strategies.py", () => {
  it("has the same keys and labels in the same order", () => {
    const py = pythonRegistry();
    expect(py.length).toBeGreaterThanOrEqual(6);
    expect(QUANT_STRATEGIES.map((s) => ({ key: s.key, label: s.label }))).toEqual(py);
  });

  it("default strategy is registered and labels resolve", () => {
    expect(isQuantStrategyKey(DEFAULT_QUANT_STRATEGY)).toBe(true);
    expect(quantLabel("inverse_vol")).toBe("Monthly inverse volatility");
    expect(quantLabel("nope")).toBe("nope");
    expect(isQuantStrategyKey("nope")).toBe(false);
  });
});
