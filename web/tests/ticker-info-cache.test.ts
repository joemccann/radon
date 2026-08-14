import { describe, it, expect } from "vitest";
import {
  canReuseUwInfo,
  hasAnyTickerData,
  isPopulated,
  pickUwInfo,
  STOCK_STATE_TTL_MS,
  stockStateRefreshDue,
} from "../lib/tickerInfoCache";

const UW = { marketcap: "83039192045", beta: "3.31" };

describe("tickerInfoCache — don't cache empty results", () => {
  describe("isPopulated", () => {
    it("is false for empty / null / undefined", () => {
      expect(isPopulated({})).toBe(false);
      expect(isPopulated(null)).toBe(false);
      expect(isPopulated(undefined)).toBe(false);
    });
    it("is true once any key is present", () => {
      expect(isPopulated(UW)).toBe(true);
    });
  });

  describe("canReuseUwInfo", () => {
    it("REFUSES to reuse an empty cached uw_info even inside the stats TTL (the RKLB bug)", () => {
      // statsCached=true (Exa 24h window alive) but uw_info was poisoned to {}
      expect(canReuseUwInfo({}, true)).toBe(false);
    });
    it("reuses a populated cached uw_info inside the stats TTL", () => {
      expect(canReuseUwInfo(UW, true)).toBe(true);
    });
    it("never reuses once the stats TTL has expired", () => {
      expect(canReuseUwInfo(UW, false)).toBe(false);
    });
  });

  describe("pickUwInfo", () => {
    it("prefers a freshly-fetched payload", () => {
      expect(pickUwInfo(UW, {})).toEqual(UW);
    });
    it("falls back to the last-good cache when the fetch came back empty", () => {
      expect(pickUwInfo({}, UW)).toEqual(UW);
    });
    it("returns {} when neither fetch nor cache has data", () => {
      expect(pickUwInfo({}, null)).toEqual({});
    });
  });

  describe("hasAnyTickerData", () => {
    it("is false only when UW + profile + stats are all empty", () => {
      expect(hasAnyTickerData({}, {}, {})).toBe(false);
    });
    it("is true when any single source has data (e.g. only the Yahoo 52W backfill)", () => {
      expect(hasAnyTickerData({}, {}, { week_52_high: 151 })).toBe(true);
      expect(hasAnyTickerData(UW, {}, {})).toBe(true);
    });
  });

  describe("stockStateRefreshDue", () => {
    const now = Date.parse("2026-08-14T15:00:00.000Z");

    it("is a 15-minute window", () => {
      expect(STOCK_STATE_TTL_MS).toBe(15 * 60 * 1000);
    });

    it("is due when no stamp exists", () => {
      expect(stockStateRefreshDue({}, now)).toBe(true);
      expect(stockStateRefreshDue(null, now)).toBe(true);
    });

    it("is not due when stock_state_checked_at is inside the TTL", () => {
      expect(stockStateRefreshDue({
        stock_state_checked_at: "2026-08-14T14:46:00.000Z",
      }, now)).toBe(false);
    });

    it("is due when stock_state_checked_at is 15 minutes old", () => {
      expect(stockStateRefreshDue({
        stock_state_checked_at: "2026-08-14T14:45:00.000Z",
      }, now)).toBe(true);
    });

    it("falls back to fetched_at for legacy entries", () => {
      expect(stockStateRefreshDue({
        fetched_at: "2026-08-14T14:50:00.000Z",
      }, now)).toBe(false);
      expect(stockStateRefreshDue({
        fetched_at: "2026-08-14T14:40:00.000Z",
      }, now)).toBe(true);
    });
  });
});
