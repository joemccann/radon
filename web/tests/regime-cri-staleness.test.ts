/**
 * Unit tests: CRI cache staleness logic — market-hours aware
 *
 * Bug: isCacheStale in /api/regime/route.ts used a fixed 60s TTL regardless
 * of market status. After close, every GET triggered a background cri_scan.py
 * even though today's EOD data was already fresh — burning API calls and
 * re-running an expensive scan unnecessarily.
 *
 * Fix: staleness is now market-hours aware:
 *  - market_open === false + data.date === today → NOT stale (EOD data is final)
 *  - market_open === true + mtime > TTL         → stale (intraday refresh)
 *  - data.date !== today                        → always stale (new trading day)
 */

import { describe, it, expect } from "vitest";
import { isCriDataStale } from "../lib/criStaleness";

// Fixed reference: 2026-03-09, 15:00 ET (market open)
const TODAY = "2026-03-09";
const RECENT_MTIME = Date.now() - 30_000;      // 30s ago — within 60s TTL
const STALE_MTIME  = Date.now() - 120_000;     // 2 min ago — past TTL

describe("isCriDataStale — market OPEN", () => {
  it("NOT stale when market open and mtime within TTL", () => {
    expect(isCriDataStale({ date: TODAY, market_open: true }, RECENT_MTIME, TODAY, true)).toBe(false);
  });

  it("stale when market open and mtime exceeds TTL", () => {
    expect(isCriDataStale({ date: TODAY, market_open: true }, STALE_MTIME, TODAY, true)).toBe(true);
  });
});

describe("isCriDataStale — market CLOSED (EOD data should never re-trigger scan)", () => {
  it("NOT stale when market closed + date is today, even with stale mtime", () => {
    expect(isCriDataStale({ date: TODAY, market_open: false }, STALE_MTIME, TODAY, false)).toBe(false);
  });

  it("NOT stale when market closed + date is today, recent mtime", () => {
    expect(isCriDataStale({ date: TODAY, market_open: false }, RECENT_MTIME, TODAY, false)).toBe(false);
  });

  it("stale when stale cache was closed but market is now open", () => {
    expect(isCriDataStale({ date: TODAY, market_open: false }, STALE_MTIME, TODAY, true)).toBe(true);
  });

  it("stale when market closed but date is YESTERDAY (new trading day)", () => {
    expect(isCriDataStale({ date: "2026-03-08", market_open: false }, STALE_MTIME, TODAY)).toBe(true);
  });
});

describe("isCriDataStale — date mismatch (always stale)", () => {
  it("stale when data date is yesterday regardless of market_open", () => {
    expect(isCriDataStale({ date: "2026-03-08", market_open: true }, RECENT_MTIME, TODAY, true)).toBe(true);
  });

  it("stale when data date is yesterday and market closed (pre-open)", () => {
    expect(isCriDataStale({ date: "2026-03-08", market_open: false }, RECENT_MTIME, TODAY, false)).toBe(true);
  });

  it("stale when data has no date field", () => {
    expect(isCriDataStale({ market_open: false }, STALE_MTIME, TODAY, false)).toBe(true);
  });
});

describe("isCriDataStale — market_open unknown/missing", () => {
  it("falls back to TTL check when market_open is undefined + date matches", () => {
    // No market_open field → use TTL (conservative: treat as open)
    expect(isCriDataStale({ date: TODAY }, RECENT_MTIME, TODAY, true)).toBe(false);
    expect(isCriDataStale({ date: TODAY }, STALE_MTIME, TODAY, true)).toBe(true);
  });
});
