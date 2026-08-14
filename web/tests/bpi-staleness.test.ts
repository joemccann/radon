/**
 * BPI as-of currency: a session is current iff it covers
 * lastCompletedSessionDate (EOD close-only, same contract as
 * isSnapshotCurrent / CRI weekend).
 *
 * Production 2026-08-13 after close showed AS OF 2026-08-12 with no
 * STALE mark. Yesterday is stale once 16:00 ET has passed; during RTH
 * yesterday is still the latest completed session; Friday stays current
 * all weekend (lessons 2026-07-11).
 */
import { describe, expect, it } from "vitest";

import { isBpiSessionCurrent } from "@/lib/bpi";
import { lastCompletedSessionDate } from "@/lib/marketSession";

const AFTER_CLOSE = new Date("2026-08-13T19:22:00-04:00");
const RTH = new Date("2026-08-13T15:00:00-04:00");
const SATURDAY = new Date("2026-08-15T12:00:00-04:00");

describe("isBpiSessionCurrent", () => {
  it("after the 16:00 ET close, today is current and yesterday is not", () => {
    expect(lastCompletedSessionDate(AFTER_CLOSE)).toBe("2026-08-13");
    expect(isBpiSessionCurrent("2026-08-13", AFTER_CLOSE)).toBe(true);
    expect(isBpiSessionCurrent("2026-08-12", AFTER_CLOSE)).toBe(false);
  });

  it("during RTH, yesterday is still current (today's close does not exist yet)", () => {
    expect(lastCompletedSessionDate(RTH)).toBe("2026-08-12");
    expect(isBpiSessionCurrent("2026-08-12", RTH)).toBe(true);
  });

  it("does not false-stale Friday on Saturday", () => {
    expect(lastCompletedSessionDate(SATURDAY)).toBe("2026-08-14");
    expect(isBpiSessionCurrent("2026-08-14", SATURDAY)).toBe(true);
  });
});
