import { describe, it, expect } from "vitest";
import { kelly } from "../wrappers/kelly";
import { hasPython313, python313Label } from "@/tests/helpers/python313";

// Every case here spawns `scripts/kelly.py` for real — that IS the contract
// being tested (the wrapper's job is the subprocess round-trip). It skips by
// name on a machine without python3.13 rather than being --exclude'd from CI,
// which used to take the file's siblings down with it. See T-276.
describe.skipIf(!hasPython313())(python313Label("kelly wrapper (live)"), () => {
  it("calculates kelly for a positive-edge trade", async () => {
    const result = await kelly({ prob: 0.6, odds: 2.0 });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.edge_exists).toBe(true);
      expect(result.data.full_kelly_pct).toBeGreaterThan(0);
      expect(result.data.recommendation).toBe("STRONG");
    }
  }, 10_000);

  it("returns DO NOT BET for negative edge", async () => {
    const result = await kelly({ prob: 0.3, odds: 1.0 });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.edge_exists).toBe(false);
      expect(result.data.recommendation).toBe("DO NOT BET");
    }
  }, 10_000);

  it("includes dollar sizing when bankroll provided", async () => {
    const result = await kelly({ prob: 0.6, odds: 2.0, bankroll: 100_000 });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.dollar_size).toBeDefined();
      expect(result.data.max_per_position).toBe(2500);
      expect(result.data.use_size).toBeDefined();
    }
  }, 10_000);

  it("respects custom fraction", async () => {
    const result = await kelly({ prob: 0.6, odds: 2.0, fraction: 0.5 });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.fraction_used).toBe(0.5);
    }
  }, 10_000);
});
