import { describe, expect, it } from "vitest";

import {
  buildDemoOptionChain,
  buildDemoOptionExpirations,
  buildDemoOptionsExposure,
} from "@/lib/demo/fixtures/options";
import { isOptionsExposurePayload } from "@/lib/optionsExposure";

const NOW = new Date("2026-09-04T18:00:00.000Z");

describe("demo options fixtures", () => {
  it("builds a bounded future expiry calendar shared by the chain", () => {
    const expirations = buildDemoOptionExpirations("amat", NOW);

    expect(expirations.symbol).toBe("AMAT");
    expect(expirations.expirations.length).toBeGreaterThanOrEqual(4);
    expect(expirations.expirations.length).toBeLessThanOrEqual(8);
    expect(expirations.expirations).toEqual([...expirations.expirations].sort());
    expect(expirations.expirations.every((expiry) => /^\d{8}$/.test(expiry))).toBe(true);
    expect(expirations.expirations.every((expiry) => expiry > "20260904")).toBe(true);

    const requestedExpiry = expirations.expirations.at(-1)!;
    const chain = buildDemoOptionChain("amat", requestedExpiry, NOW);
    expect(chain).toMatchObject({
      symbol: "AMAT",
      expiry: requestedExpiry,
      exchange: "SMART",
      multiplier: "100",
    });
    expect(chain.expirations).toEqual(expirations.expirations);
    expect(chain.strikes).toContain(400);
    expect(chain.strikes).toContain(485);
    expect(chain.strikes).toEqual([...chain.strikes].sort((a, b) => a - b));
  });

  it("builds a current, schema-valid exposure cube with coherent dimensions", () => {
    const payload = buildDemoOptionsExposure("aapl", "intraday", NOW);

    expect(isOptionsExposurePayload(payload)).toBe(true);
    expect(payload.symbol).toBe("AAPL");
    expect(payload.frequency).toBe("intraday");
    expect(payload.source_time).toBe(NOW.toISOString());
    expect(payload.fetched_at).toBe(NOW.toISOString());
    expect(payload.expirations.every(({ dte }) => dte > 0)).toBe(true);
    expect(payload.cells.strike_idx).toHaveLength(
      payload.strikes.length * payload.expirations.length,
    );
  });
});
