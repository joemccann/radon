import { describe, it, expect } from "vitest";
import { isGlobexOpen } from "@/lib/futuresSession";

/**
 * Build a Date that reads as the given wall-clock time in America/New_York.
 * We pass an explicit `-04:00`/`-05:00` offset so the test is DST-correct for
 * the chosen date (June = EDT = -04:00).
 */
function etJune(dayTime: string): Date {
  // June 2026 is EDT (-04:00). e.g. "2026-06-09T02:00" (a Tuesday).
  return new Date(`${dayTime}:00-04:00`);
}

describe("isGlobexOpen — CME equity-index futures session", () => {
  it("open Tuesday overnight (02:00 ET)", () => {
    expect(isGlobexOpen(etJune("2026-06-09T02:00"))).toBe(true);
  });

  it("open Tuesday midday (12:00 ET)", () => {
    expect(isGlobexOpen(etJune("2026-06-09T12:00"))).toBe(true);
  });

  it("CLOSED during the daily 17:00-18:00 ET maintenance halt (Tue 17:30)", () => {
    expect(isGlobexOpen(etJune("2026-06-09T17:30"))).toBe(false);
  });

  it("re-opens after maintenance (Tue 18:30 ET)", () => {
    expect(isGlobexOpen(etJune("2026-06-09T18:30"))).toBe(true);
  });

  it("CLOSED Saturday all day", () => {
    expect(isGlobexOpen(etJune("2026-06-13T12:00"))).toBe(false);
  });

  it("CLOSED Sunday before 18:00 ET", () => {
    expect(isGlobexOpen(etJune("2026-06-14T12:00"))).toBe(false);
  });

  it("OPEN Sunday after 18:00 ET (weekly open)", () => {
    expect(isGlobexOpen(etJune("2026-06-14T19:00"))).toBe(true);
  });

  it("OPEN Friday before 17:00 ET", () => {
    expect(isGlobexOpen(etJune("2026-06-12T16:00"))).toBe(true);
  });

  it("CLOSED Friday after 17:00 ET (weekly close, no reopen)", () => {
    expect(isGlobexOpen(etJune("2026-06-12T17:30"))).toBe(false);
    expect(isGlobexOpen(etJune("2026-06-12T19:00"))).toBe(false);
  });
});
