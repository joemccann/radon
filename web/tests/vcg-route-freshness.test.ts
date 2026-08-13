import { describe, expect, it } from "vitest";
import { expectedVcgSessionDate } from "@/app/api/vcg/route";

describe("VCG route session freshness", () => {
  it("weekend_preopen_and_holiday_cache_do_not_rescan", () => {
    expect(expectedVcgSessionDate(new Date("2026-08-15T16:00:00Z"))).toBe("2026-08-14");
    expect(expectedVcgSessionDate(new Date("2026-08-17T12:00:00Z"))).toBe("2026-08-14");
    expect(expectedVcgSessionDate(new Date("2026-07-03T17:00:00Z"))).toBe("2026-07-02");
  });
});
