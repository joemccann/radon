import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { needsCurrentEtSessionRetry } from "../lib/useRegime";

describe("needsCurrentEtSessionRetry", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-22T14:00:00-04:00"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("retries when the same-day CRI scan is stale intraday", () => {
    expect(
      needsCurrentEtSessionRetry({
        date: "2026-04-22",
        market_open: true,
        scan_time: new Date(Date.now() - 120_000).toISOString(),
      }),
    ).toBe(true);
  });

  it("does not retry when the same-day CRI scan is still fresh intraday", () => {
    expect(
      needsCurrentEtSessionRetry({
        date: "2026-04-22",
        market_open: true,
        scan_time: new Date(Date.now() - 20_000).toISOString(),
      }),
    ).toBe(false);
  });

  it("retries when the CRI cache is from a prior ET session", () => {
    expect(
      needsCurrentEtSessionRetry({
        date: "2026-04-21",
        market_open: false,
        scan_time: new Date("2026-04-21T20:00:00Z").toISOString(),
      }),
    ).toBe(true);
  });
});
