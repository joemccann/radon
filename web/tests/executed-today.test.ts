import { describe, expect, it } from "vitest";
import {
  etCalendarDateString,
  filterExecutedToEtToday,
  formatExecutedFillTime,
  isFillOnEtCalendarDay,
} from "../lib/orders/executedToday";

describe("etCalendarDateString", () => {
  it("formats a known instant as ET calendar day", () => {
    // 2026-07-09 06:00 UTC = 02:00 ET (EDT) → still July 9
    expect(etCalendarDateString(new Date("2026-07-09T06:00:00.000Z"))).toBe("2026-07-09");
    // 2026-07-09 03:00 UTC = 23:00 ET July 8
    expect(etCalendarDateString(new Date("2026-07-09T03:00:00.000Z"))).toBe("2026-07-08");
  });
});

describe("isFillOnEtCalendarDay", () => {
  const now = new Date("2026-07-09T14:00:00.000Z"); // 10:00 ET
  const today = "2026-07-09";

  it("accepts fills on the same ET day", () => {
    expect(isFillOnEtCalendarDay("2026-07-09T10:31:16-04:00", today, now)).toBe(true);
    expect(isFillOnEtCalendarDay("2026-07-09T06:54:18-04:00", today, now)).toBe(true);
  });

  it("rejects fills from the previous ET day", () => {
    // Yesterday 10:31 ET — the MSFT bug case
    expect(isFillOnEtCalendarDay("2026-07-08T10:31:16-04:00", today, now)).toBe(false);
  });

  it("handles date-only strings as ET day labels", () => {
    expect(isFillOnEtCalendarDay("2026-07-09", today, now)).toBe(true);
    expect(isFillOnEtCalendarDay("2026-07-08", today, now)).toBe(false);
  });
});

describe("filterExecutedToEtToday", () => {
  it("drops prior-day MSFT-style fills while keeping same-day session fills", () => {
    const now = new Date("2026-07-09T11:00:00.000Z"); // 07:00 ET
    const fills = [
      { time: "2026-07-09T06:54:18-04:00", symbol: "MU" },
      { time: "2026-07-08T10:31:16-04:00", symbol: "MSFT" },
      { time: "2026-07-09T06:30:05-04:00", symbol: "MU" },
    ];
    const out = filterExecutedToEtToday(fills, now);
    expect(out.map((f) => f.symbol)).toEqual(["MU", "MU"]);
  });
});

describe("formatExecutedFillTime", () => {
  const now = new Date("2026-07-09T14:00:00.000Z");

  it("shows clock only for today ET", () => {
    const s = formatExecutedFillTime("2026-07-09T06:54:18-04:00", now);
    expect(s).toMatch(/6:54:18/);
    expect(s).not.toMatch(/7\//);
  });

  it("prefixes date for prior ET day fills", () => {
    const s = formatExecutedFillTime("2026-07-08T10:31:16-04:00", now);
    expect(s).toMatch(/7\/8/);
    expect(s).toMatch(/10:31:16/);
  });
});
