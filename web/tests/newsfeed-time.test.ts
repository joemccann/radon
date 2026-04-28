import { describe, expect, it, vi } from "vitest";
import { formatAbsolute, formatTime, formatRelative } from "../lib/newsfeedTime";

describe("newsfeedTime", () => {
  it("formats local-noon as '12:XX PM' (regression: dashboard showed 12:20 AM at noon)", () => {
    // 12:20 in the host's local timezone. Whatever TZ the test runs under,
    // the wall-clock hour is 12 and the formatter must report PM.
    const localNoon = new Date(2026, 3, 28, 12, 20, 0);
    expect(formatTime(localNoon.toISOString())).toBe("12:20 PM");
  });

  it("formats local-midnight as '12:XX AM'", () => {
    const localMidnight = new Date(2026, 3, 28, 0, 20, 0);
    expect(formatTime(localMidnight.toISOString())).toBe("12:20 AM");
  });

  it("formatAbsolute pins month, day, hour12 — deterministic across locales", () => {
    const localNoon = new Date(2026, 3, 28, 12, 20, 0);
    expect(formatAbsolute(localNoon.toISOString())).toBe("Apr 28, 12:20 PM");
  });

  it("does not depend on the host locale (pins explicit locale + hour12)", () => {
    // Capture constructor args without breaking format() — wrap, don't replace.
    const real = Intl.DateTimeFormat;
    const calls: Array<{ locale: unknown; opts: Intl.DateTimeFormatOptions | undefined }> = [];
    const wrapped = function (locale?: unknown, opts?: Intl.DateTimeFormatOptions) {
      calls.push({ locale, opts });
      // @ts-expect-error — forwarding to real Intl.DateTimeFormat constructor
      return new real(locale, opts);
    } as unknown as typeof Intl.DateTimeFormat;
    // @ts-expect-error — runtime override for the duration of the test
    Intl.DateTimeFormat = wrapped;
    try {
      formatTime("2026-04-28T09:20:00.000Z");
      formatAbsolute("2026-04-28T09:20:00.000Z");
    } finally {
      // @ts-expect-error — restore
      Intl.DateTimeFormat = real;
    }
    expect(calls.length).toBeGreaterThan(0);
    for (const { locale, opts } of calls) {
      expect(typeof locale).toBe("string");
      expect(opts?.hour12).toBe(true);
    }
  });

  it("returns 'moments ago' under one minute and pluralises minutes/hours/days", () => {
    const now = Date.UTC(2026, 3, 28, 12, 0, 0);
    expect(formatRelative(new Date(now - 30_000).toISOString(), now)).toBe("moments ago");
    expect(formatRelative(new Date(now - 1 * 60_000).toISOString(), now)).toBe("1 minute ago");
    expect(formatRelative(new Date(now - 3 * 60_000).toISOString(), now)).toBe("3 minutes ago");
    expect(formatRelative(new Date(now - 2 * 3_600_000).toISOString(), now)).toBe("2 hours ago");
    expect(formatRelative(new Date(now - 3 * 86_400_000).toISOString(), now)).toBe("3 days ago");
  });

  it("returns '' for invalid timestamps in formatTime/formatRelative", () => {
    expect(formatTime("not-a-date")).toBe("");
    expect(formatRelative("not-a-date")).toBe("");
    // formatAbsolute returns the original string for bad input, matching the legacy contract
    expect(formatAbsolute("not-a-date")).toBe("not-a-date");
  });
});
