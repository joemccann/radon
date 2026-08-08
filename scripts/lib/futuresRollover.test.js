import { describe, expect, it } from "vitest";

import {
  FUTURES_RECHECK_TTL_MS,
  FUTURES_RERESOLVE_MIN_GAP_MS,
  etDateYYYYMMDD,
  isResolvedFutureStale,
  pickNearestExpiry,
  shouldAttemptReresolve,
} from "./futuresRollover.js";

const HOUR = 3_600_000;
const NOW = Date.UTC(2026, 7, 12, 18, 0, 0); // 2026-08-12 14:00 ET
const ET_TODAY = "20260812";

function fut(expiry, extra = {}) {
  return { symbol: "VIX", lastTradeDateOrContractMonth: expiry, conId: Number(expiry), ...extra };
}

describe("isResolvedFutureStale — the resolved expiry rolled", () => {
  it("is stale once the cached expiry is behind ET today", () => {
    expect(isResolvedFutureStale("20260811", NOW - HOUR, NOW, ET_TODAY)).toBe(true);
  });

  it("is stale for a long-past expiry even with a brand-new resolution timestamp", () => {
    expect(isResolvedFutureStale("20250115", NOW, NOW, ET_TODAY)).toBe(true);
  });

  it("is NOT stale when the expiry IS ET today (still listed, still the session's contract)", () => {
    // Documented boundary: sortedNonExpiredChain admits expiry >= today, and a
    // same-day future still quotes (and is still the settlement reference) until
    // the session ends. Rolling at 00:00 ET rather than at the bell means the
    // curve is never re-resolved out from under a live session.
    expect(isResolvedFutureStale(ET_TODAY, NOW - HOUR, NOW, ET_TODAY)).toBe(false);
  });

  it("is NOT stale for a future-dated expiry inside the TTL", () => {
    expect(isResolvedFutureStale("20260916", NOW - HOUR, NOW, ET_TODAY)).toBe(false);
  });
});

describe("isResolvedFutureStale — re-check TTL", () => {
  it("is stale at EXACTLY the TTL", () => {
    expect(isResolvedFutureStale("20260916", NOW - FUTURES_RECHECK_TTL_MS, NOW, ET_TODAY)).toBe(true);
  });

  it("is NOT stale one millisecond under the TTL", () => {
    expect(isResolvedFutureStale("20260916", NOW - FUTURES_RECHECK_TTL_MS + 1, NOW, ET_TODAY)).toBe(false);
  });

  it("is stale well past the TTL", () => {
    expect(isResolvedFutureStale("20260916", NOW - 5 * FUTURES_RECHECK_TTL_MS, NOW, ET_TODAY)).toBe(true);
  });

  it("re-checks at least twice a trading day", () => {
    expect(FUTURES_RECHECK_TTL_MS).toBeLessThanOrEqual(12 * HOUR);
    expect(FUTURES_RECHECK_TTL_MS).toBeGreaterThanOrEqual(HOUR);
  });

  it("tolerates a backwards clock (never negative-stale)", () => {
    expect(isResolvedFutureStale("20260916", NOW + HOUR, NOW, ET_TODAY)).toBe(false);
  });
});

describe("isResolvedFutureStale — unusable inputs re-resolve", () => {
  it.each([undefined, null, "", "2026", "202609", "not-a-date"])(
    "treats %o as an unusable cached expiry",
    (expiry) => {
      expect(isResolvedFutureStale(expiry, NOW, NOW, ET_TODAY)).toBe(true);
    },
  );

  it.each([undefined, null, NaN])("treats %o as an unknown resolution time", (resolvedAt) => {
    expect(isResolvedFutureStale("20260916", resolvedAt, NOW, ET_TODAY)).toBe(true);
  });

  it("falls back to the TTL rule when ET today is unusable", () => {
    expect(isResolvedFutureStale("20260916", NOW - HOUR, NOW, "")).toBe(false);
    expect(isResolvedFutureStale("20260916", NOW - FUTURES_RECHECK_TTL_MS, NOW, "")).toBe(true);
  });

  it("accepts a dashed date shape for either side", () => {
    expect(isResolvedFutureStale("2026-08-11", NOW - HOUR, NOW, "2026-08-12")).toBe(true);
    expect(isResolvedFutureStale("2026-08-13", NOW - HOUR, NOW, "2026-08-12")).toBe(false);
  });
});

describe("shouldAttemptReresolve — bounds a failing re-resolve", () => {
  it("allows the first attempt", () => {
    expect(shouldAttemptReresolve(undefined, NOW)).toBe(true);
    expect(shouldAttemptReresolve(NaN, NOW)).toBe(true);
  });

  it("suppresses a second attempt inside the gap", () => {
    expect(shouldAttemptReresolve(NOW - FUTURES_RERESOLVE_MIN_GAP_MS + 1, NOW)).toBe(false);
  });

  it("allows another attempt at EXACTLY the gap", () => {
    expect(shouldAttemptReresolve(NOW - FUTURES_RERESOLVE_MIN_GAP_MS, NOW)).toBe(true);
  });

  it("keeps the retry gap far below the re-check TTL", () => {
    expect(FUTURES_RERESOLVE_MIN_GAP_MS).toBeGreaterThan(0);
    expect(FUTURES_RERESOLVE_MIN_GAP_MS).toBeLessThan(FUTURES_RECHECK_TTL_MS);
  });
});

describe("etDateYYYYMMDD", () => {
  it("uses the America/New_York date, not UTC", () => {
    // 2026-08-13 01:30 UTC is still 2026-08-12 21:30 in New York.
    expect(etDateYYYYMMDD(Date.UTC(2026, 7, 13, 1, 30))).toBe("20260812");
  });

  it("rolls at ET midnight", () => {
    expect(etDateYYYYMMDD(Date.UTC(2026, 7, 13, 3, 59))).toBe("20260812");
    expect(etDateYYYYMMDD(Date.UTC(2026, 7, 13, 4, 1))).toBe("20260813");
  });

  it("accepts a Date as well as epoch ms", () => {
    expect(etDateYYYYMMDD(new Date(Date.UTC(2026, 7, 12, 18, 0)))).toBe("20260812");
  });

  it("emits 8 digits", () => {
    expect(etDateYYYYMMDD(NOW)).toMatch(/^\d{8}$/);
  });
});

describe("pickNearestExpiry — never prices a live option off a future that dies first", () => {
  it("prefers the future expiring ON the option expiry", () => {
    const chain = [fut("20260819"), fut("20260916"), fut("20261021")];
    expect(pickNearestExpiry(chain, "20260916").conId).toBe(20260916);
  });

  it("prefers a LATER future over a nearer EARLIER one", () => {
    // Option 2026-09-09. Earlier future is 7 days away, later future is 7 days
    // away — the tie used to hand back the earlier (already-dead-by-then)
    // contract because the scan kept the first strict minimum.
    const chain = [fut("20260902"), fut("20260916")];
    expect(pickNearestExpiry(chain, "20260909").conId).toBe(20260916);
  });

  it("prefers a later future even when an earlier one is strictly nearer", () => {
    // Option 2026-09-04: earlier future 2 days back, later future 12 days out.
    // The later one is the only one still quoting when the option expires.
    const chain = [fut("20260902"), fut("20260916")];
    expect(pickNearestExpiry(chain, "20260904").conId).toBe(20260916);
  });

  it("picks the NEAREST of several later futures", () => {
    const chain = [fut("20260902"), fut("20260916"), fut("20261021"), fut("20261118")];
    expect(pickNearestExpiry(chain, "20260904").conId).toBe(20260916);
  });

  it("falls back to the nearest earlier future when the option outlives the whole chain", () => {
    const chain = [fut("20260819"), fut("20260916")];
    expect(pickNearestExpiry(chain, "20270115").conId).toBe(20260916);
  });

  it("ignores chain entries with an unusable expiry", () => {
    const chain = [fut(""), { symbol: "VIX", conId: 1 }, fut("20260916")];
    expect(pickNearestExpiry(chain, "20260910").conId).toBe(20260916);
  });

  it("returns null for an empty or non-array chain", () => {
    expect(pickNearestExpiry([], "20260916")).toBeNull();
    expect(pickNearestExpiry(undefined, "20260916")).toBeNull();
    expect(pickNearestExpiry(null, "20260916")).toBeNull();
  });

  it("falls back to the front of the chain for an unusable option expiry", () => {
    const chain = [fut("20260819"), fut("20260916")];
    expect(pickNearestExpiry(chain, "").conId).toBe(20260819);
    expect(pickNearestExpiry(chain, "2026").conId).toBe(20260819);
  });

  it("falls back to the front of the chain when no entry has a readable expiry", () => {
    const chain = [{ symbol: "VIX", conId: 7 }];
    expect(pickNearestExpiry(chain, "20260916").conId).toBe(7);
  });

  it("reads the expiry from the first 8 digits of lastTradeDateOrContractMonth", () => {
    const chain = [fut("20260916 15:00:00", { conId: 916 }), fut("20261021")];
    expect(pickNearestExpiry(chain, "20260916").conId).toBe(916);
  });
});
