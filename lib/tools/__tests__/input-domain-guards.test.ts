import { beforeEach, describe, expect, it, vi } from "vitest";

const { runScript } = vi.hoisted(() => ({
  runScript: vi.fn(async () => ({ ok: true, data: {} })),
}));
vi.mock("../runner", () => ({ runScript }));

import { kelly } from "../wrappers/kelly";
import { scanner } from "../wrappers/scanner";
import { vcgScan } from "../wrappers/vcg-scan";

describe("tool wrapper domain guards", () => {
  beforeEach(() => runScript.mockClear());

  it.each([
    { prob: -0.1, odds: 2 },
    { prob: 1.1, odds: 2 },
    { prob: 0.6, odds: 0 },
    { prob: 0.6, odds: 2, fraction: 0 },
    { prob: 0.6, odds: 2, bankroll: -1 },
  ])("rejects invalid Kelly input before spawning %#", async (input) => {
    await expect(kelly(input)).rejects.toThrow(RangeError);
    expect(runScript).not.toHaveBeenCalled();
  });

  it.each([0, 1.5, 501])("rejects invalid scanner top %s before spawning", async (top) => {
    await expect(scanner({ top })).rejects.toThrow(RangeError);
    expect(runScript).not.toHaveBeenCalled();
  });

  it("rejects invalid VCG controls before spawning", async () => {
    await expect(vcgScan({ proxy: "SPY" as "HYG" })).rejects.toThrow(RangeError);
    await expect(vcgScan({ days: 0 })).rejects.toThrow(RangeError);
    await expect(vcgScan({ days: 1.5 })).rejects.toThrow(RangeError);
    expect(runScript).not.toHaveBeenCalled();
  });
});
