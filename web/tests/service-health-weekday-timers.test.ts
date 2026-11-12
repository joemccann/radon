/**
 * @vitest-environment node
 *
 * Weekday-only timers (radon-cta-sync.timer, radon-leap.timer: Mon..Fri) must
 * read fresh across the weekend and on Monday before the first Monday run,
 * and stale once a whole trading day passes without a run. Their age is
 * counted on trading days only, so the window states one trading day of
 * cadence instead of a weekend-sized wall-clock number.
 */
import { describe, it, expect } from "vitest";

import { getMarketStateFromDate, isStale } from "../lib/serviceHealthWindows";

const staleAt = (service: string, updatedAt: string, nowIso: string): boolean => {
  const now = new Date(nowIso);
  return isStale(service, updatedAt, getMarketStateFromDate(now), now.getTime());
};

// Fri 2026-11-13, Mon 2026-11-16 and Tue 2026-11-17 are trading days (EST, UTC-5).
const CTA_FRI_LAST_RUN = "2026-11-13T21:30:00Z"; // last radon-cta-sync.timer slot
const LEAP_FRI_RUN = "2026-11-13T15:00:00Z"; // 10:00 ET radon-leap.timer slot
const CTA_SERVICES = ["cta-sync", "menthorq-cta"];

describe("weekday-only timer rows span the weekend", () => {
  it.each(CTA_SERVICES)("%s is fresh through Saturday and Sunday", (service) => {
    expect(staleAt(service, CTA_FRI_LAST_RUN, "2026-11-14T17:00:00Z")).toBe(false);
    expect(staleAt(service, CTA_FRI_LAST_RUN, "2026-11-16T03:00:00Z")).toBe(false);
  });

  it.each(CTA_SERVICES)("%s is fresh Monday 05:00 ET after a Friday run", (service) => {
    expect(staleAt(service, CTA_FRI_LAST_RUN, "2026-11-16T10:00:00Z")).toBe(false);
  });

  it.each(CTA_SERVICES)("%s is fresh Monday midday before the 13:15 ET first run", (service) => {
    expect(staleAt(service, CTA_FRI_LAST_RUN, "2026-11-16T18:00:00Z")).toBe(false);
  });

  it.each(CTA_SERVICES)("%s is stale Tuesday after a missed Monday", (service) => {
    expect(staleAt(service, CTA_FRI_LAST_RUN, "2026-11-17T10:00:00Z")).toBe(true);
    expect(staleAt(service, CTA_FRI_LAST_RUN, "2026-11-17T17:00:00Z")).toBe(true);
  });

  it("leap-scan is fresh Monday 05:00 ET and Monday 09:45 ET before the 10:00 ET run", () => {
    expect(staleAt("leap-scan", LEAP_FRI_RUN, "2026-11-16T10:00:00Z")).toBe(false);
    expect(staleAt("leap-scan", LEAP_FRI_RUN, "2026-11-16T14:45:00Z")).toBe(false);
  });

  it("leap-scan is stale Tuesday after a missed Monday", () => {
    expect(staleAt("leap-scan", LEAP_FRI_RUN, "2026-11-17T10:00:00Z")).toBe(true);
    expect(staleAt("leap-scan", LEAP_FRI_RUN, "2026-11-17T15:30:00Z")).toBe(true);
  });

  it("a full-closure Monday holiday is skipped like the weekend (MLK 2026-01-19)", () => {
    expect(staleAt("cta-sync", "2026-01-16T21:30:00Z", "2026-01-20T10:00:00Z")).toBe(false);
    expect(staleAt("cta-sync", "2026-01-16T21:30:00Z", "2026-01-21T10:00:00Z")).toBe(true);
  });

  it("goes stale once one trading day of silence passes the 25h window", () => {
    expect(staleAt("cta-sync", "2026-11-17T21:30:00Z", "2026-11-18T22:00:00Z")).toBe(false);
    expect(staleAt("cta-sync", "2026-11-17T21:30:00Z", "2026-11-18T22:45:00Z")).toBe(true);
  });

  it("counts Monday from ET midnight across the spring-forward weekend (2026-03-08)", () => {
    // Fri 2026-03-06 21:30 UTC = 16:30 EST leaves 7.5h of Friday. Monday is EDT.
    // Mon 17:00 EDT (21:00 UTC) = 7.5 + 17 = 24.5h; Mon 18:00 EDT (22:00 UTC) = 25.5h.
    expect(staleAt("cta-sync", "2026-03-06T21:30:00Z", "2026-03-09T21:00:00Z")).toBe(false);
    expect(staleAt("cta-sync", "2026-03-06T21:30:00Z", "2026-03-09T22:00:00Z")).toBe(true);
  });
});
