import { describe, expect, it } from "vitest";
import {
  isPortfolioSnapshotUnexpectedlyStale,
  PORTFOLIO_SNAPSHOT_STALE_WARNING,
} from "../lib/portfolioSnapshotFreshness";

describe("isPortfolioSnapshotUnexpectedlyStale", () => {
  // Friday 2026-07-10 15:00 ET = 19:00 UTC (RTH open)
  const fridayRth = Date.parse("2026-07-10T19:00:00.000Z");
  // Saturday 2026-07-11 14:00 ET = 18:00 UTC (closed)
  const saturday = Date.parse("2026-07-11T18:00:00.000Z");

  it("treats missing timestamp as stale", () => {
    expect(isPortfolioSnapshotUnexpectedlyStale(null, fridayRth)).toBe(true);
    expect(isPortfolioSnapshotUnexpectedlyStale(undefined, fridayRth)).toBe(true);
  });

  it("allows a few-minute-old snapshot during RTH (within 10m window)", () => {
    const fiveMinOld = fridayRth - 5 * 60_000;
    expect(isPortfolioSnapshotUnexpectedlyStale(fiveMinOld, fridayRth)).toBe(false);
  });

  it("flags a snapshot older than the RTH 10m portfolio-sync window", () => {
    const fifteenMinOld = fridayRth - 15 * 60_000;
    expect(isPortfolioSnapshotUnexpectedlyStale(fifteenMinOld, fridayRth)).toBe(true);
  });

  it("does not flag Friday close snapshot on Saturday (closed 3d window)", () => {
    // Friday ~16:00 ET close-ish sample stored as 20:00 UTC
    const fridayClose = Date.parse("2026-07-10T20:00:00.000Z");
    expect(isPortfolioSnapshotUnexpectedlyStale(fridayClose, saturday)).toBe(false);
  });

  it("flags multi-day silence even when market is closed", () => {
    const fourDaysOld = saturday - 4 * 24 * 60 * 60_000;
    expect(isPortfolioSnapshotUnexpectedlyStale(fourDaysOld, saturday)).toBe(true);
  });
});

describe("PORTFOLIO_SNAPSHOT_STALE_WARNING", () => {
  it("does not use the old always-on 60s phrasing", () => {
    expect(PORTFOLIO_SNAPSHOT_STALE_WARNING).not.toMatch(/is stale; live sync/);
    expect(PORTFOLIO_SNAPSHOT_STALE_WARNING).toMatch(/scheduled refresh window/i);
  });
});
