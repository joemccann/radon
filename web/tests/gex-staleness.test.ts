/**
 * Unit tests: GEX cache staleness logic — market-hours aware
 *
 * Same pattern as VCG/CRI staleness but anchored to scan_time.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { isGexDataStale } from "../lib/gexStaleness";

const TODAY = "2026-04-06";
const YESTERDAY = "2026-04-05";

describe("isGexDataStale — market OPEN", () => {
  beforeEach(() => {
    // Fix Date.now to a known value for age calculations
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-06T14:00:00-04:00")); // 2pm ET
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("NOT stale when market open and scan_time within TTL", () => {
    const recentScanTime = new Date(Date.now() - 30_000).toISOString(); // 30s ago
    expect(isGexDataStale({ scan_time: recentScanTime }, TODAY, true)).toBe(false);
  });

  it("stale when market open and scan_time exceeds TTL", () => {
    const staleScanTime = new Date(Date.now() - 120_000).toISOString(); // 2 min ago
    expect(isGexDataStale({ scan_time: staleScanTime }, TODAY, true)).toBe(true);
  });
});

describe("isGexDataStale — market CLOSED", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-06T18:00:00-04:00")); // 6pm ET
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("NOT stale when market closed + scan date is today", () => {
    const scanTime = new Date("2026-04-06T16:05:00-04:00").toISOString();
    expect(isGexDataStale({ scan_time: scanTime }, TODAY, false)).toBe(false);
  });

  it("NOT stale even with old scan_time if same day and market closed", () => {
    const scanTime = new Date("2026-04-06T10:00:00-04:00").toISOString();
    expect(isGexDataStale({ scan_time: scanTime }, TODAY, false)).toBe(false);
  });
});

describe("isGexDataStale — date mismatch (always stale)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-06T14:00:00-04:00"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("stale when scan date is yesterday", () => {
    const scanTime = new Date("2026-04-05T15:00:00-04:00").toISOString();
    expect(isGexDataStale({ scan_time: scanTime }, TODAY, true)).toBe(true);
  });

  it("stale when scan date is yesterday and market closed", () => {
    const scanTime = new Date("2026-04-05T16:00:00-04:00").toISOString();
    expect(isGexDataStale({ scan_time: scanTime }, TODAY, false)).toBe(true);
  });
});

describe("isGexDataStale — missing/invalid data", () => {
  it("stale when scan_time is missing", () => {
    expect(isGexDataStale({}, TODAY, true)).toBe(true);
  });

  it("stale when scan_time is empty string", () => {
    expect(isGexDataStale({ scan_time: "" }, TODAY, true)).toBe(true);
  });

  it("stale when scan_time is invalid", () => {
    expect(isGexDataStale({ scan_time: "not-a-date" }, TODAY, true)).toBe(true);
  });
});
