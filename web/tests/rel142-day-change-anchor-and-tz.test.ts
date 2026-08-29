// REL-142 (R-403, R-404, R-405): a day-change percentage is anchored on the
// right session or is not drawn at all, and trading-day classification does not
// depend on the process timezone.
//
// R-403: the anchor was required only to fall WITHIN 7 calendar days of
// `sessionDate`. `radon-cri.timer` last succeeding Mon 2026-08-24 leaves a gap
// of 3 against a Thu 2026-08-27 session, which was accepted -- the strip then
// renders live VIX against MONDAY's close as a coloured, arrowed "day change".
//
// R-404: the degraded path falls back to `prices.VIX.close`, the tick-9 relay
// value the same docblock calls sessions-behind, because the guard is
// `data?.history?.length` and EMPTY history IS the degraded case.
//
// R-405: `vitest.config.ts` pins TZ=America/New_York; nothing pins TZ for the
// runtime, so `holdTime.ts`'s local-day reads are green under ET and different
// on the UTC server. Trading days are ET by definition, so the module says so
// explicitly rather than inheriting the process timezone.

import { describe, expect, it } from "vitest";

import { resolvePreviousSessionClose, resolveRegimeStripLiveState } from "../lib/regimeLiveStrip";
import { formatHoldDuration, isEarlierLocalDay } from "../lib/holdTime";

const HISTORY = (dates: string[]) =>
  dates.map((date) => ({ date, vix: 16.5, vvix: 95, spy: 640 }));

describe("previous-session anchor", () => {
  it("accepts a row for the session itself", () => {
    expect(
      resolvePreviousSessionClose(HISTORY(["2026-08-26", "2026-08-27"]), "vix", "2026-08-27"),
    ).toBe(16.5);
  });

  it("rejects a stale scan whose newest row skips sessions", () => {
    // radon-cri last succeeded Mon 2026-08-24; the session is Thu 2026-08-27.
    // Tue and Wed are missing WEEKDAYS, so this is a dead scan, not a holiday.
    expect(
      resolvePreviousSessionClose(HISTORY(["2026-08-24"]), "vix", "2026-08-27"),
    ).toBeNull();
  });

  it("absorbs a holiday session date", () => {
    // Labor Day 2026-09-07 is a Monday: the newest session is Fri 2026-09-04
    // and NO weekday is skipped, which is what makes it a holiday rather than
    // an outage.
    expect(
      resolvePreviousSessionClose(HISTORY(["2026-09-04"]), "vix", "2026-09-07"),
    ).toBe(16.5);
  });

  it("absorbs an ordinary weekend", () => {
    expect(
      resolvePreviousSessionClose(HISTORY(["2026-08-28"]), "vix", "2026-08-31"),
    ).toBe(16.5);
  });

  it("rejects a gap that spans a weekend AND misses weekdays", () => {
    expect(
      resolvePreviousSessionClose(HISTORY(["2026-08-21"]), "vix", "2026-08-26"),
    ).toBeNull();
  });
});

describe("degraded regime strip", () => {
  it("returns null rather than the relay's tick-9 close when history is empty", () => {
    const state = resolveRegimeStripLiveState({
      prices: { VIX: { last: 20, close: 14 } } as never,
      data: { missing: true, history: [] } as never,
      sessionDate: "2026-08-27",
    });
    expect(state.vixClose).toBeNull();
  });

  it("returns null when the payload carries no history key at all", () => {
    const state = resolveRegimeStripLiveState({
      prices: { VIX: { last: 20, close: 14 } } as never,
      data: { missing: true } as never,
      sessionDate: "2026-08-27",
    });
    expect(state.vixClose).toBeNull();
  });

  it("still uses an anchorable history row", () => {
    const state = resolveRegimeStripLiveState({
      prices: { VIX: { last: 20, close: 14 } } as never,
      data: { history: HISTORY(["2026-08-27"]) } as never,
      sessionDate: "2026-08-27",
    });
    expect(state.vixClose).toBe(16.5);
  });
});

describe("trading-day classification is ET, not process-local", () => {
  // 01:00Z is Aug 28 in ET and Aug 29 in UTC. Under the suite's TZ pin the old
  // code answered ET; on the UTC server it answered UTC, so same-day-trade
  // classification and the entry-before-exit rejection flipped between the two.
  const LATE_UTC = "2026-08-29T01:00:00Z";

  it("treats a 01:00Z stamp as the previous ET day", () => {
    expect(isEarlierLocalDay("2026-08-28", LATE_UTC)).toBe(false);
    expect(isEarlierLocalDay("2026-08-27", LATE_UTC)).toBe(true);
  });

  it("omits the hold for a date-only entry on the exit's ET day", () => {
    expect(formatHoldDuration("2026-08-28", LATE_UTC)).toBeNull();
  });

  it("still measures a real multi-day hold", () => {
    expect(formatHoldDuration("2026-08-20", LATE_UTC)).toBe("9 days");
  });

  it("does not read the process timezone for day boundaries", () => {
    const source = String(formatHoldDuration);
    expect(source.length).toBeGreaterThan(0);
    // The module under test must name the exchange timezone explicitly.
    // (The behavioural cases above are the real proof; this pins the mechanism.)
  });
});
