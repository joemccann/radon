/**
 * The place-route map must match FUTURES_ROOTS in contract_resolver.py.
 * ES/NQ/RTY are futures symbols inside that table, not keys.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { FUTURES_ROOT_MULTIPLIERS } from "../lib/futuresRootMultipliers";

describe("FUTURES_ROOT_MULTIPLIERS", () => {
  it("matches the Python FUTURES_ROOTS multipliers", () => {
    const py = readFileSync(
      path.resolve(__dirname, "../../scripts/clients/contract_resolver.py"),
      "utf8",
    );
    const start = py.indexOf("FUTURES_ROOTS");
    const end = py.indexOf("def supports_futures");
    const block = py.slice(start, end);
    const fromPython = Object.fromEntries(
      [...block.matchAll(/"([A-Z]+)":\s*\{[^}]*"multiplier":\s*"(\d+)"/g)].map((match) => [
        match[1],
        Number(match[2]),
      ]),
    );
    expect(fromPython).toEqual({ VIX: 1000, SPX: 50, NDX: 20, RUT: 50 });
    expect(FUTURES_ROOT_MULTIPLIERS).toEqual(fromPython);
    expect(FUTURES_ROOT_MULTIPLIERS).not.toHaveProperty("ES");
    expect(FUTURES_ROOT_MULTIPLIERS).not.toHaveProperty("NQ");
    expect(FUTURES_ROOT_MULTIPLIERS).not.toHaveProperty("RTY");
  });
});