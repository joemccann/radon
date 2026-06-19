import { describe, it, expect } from "vitest";
import { resolveMarketState, marketState, isMarketOpen } from "./marketCalendar.js";

describe("resolveMarketState (pure core)", () => {
  const base = { minutes: 11 * 60, weekday: 3, ibkrDay: null, isStaticHoliday: false }; // Wed 11:00

  it("normal weekday RTH → open", () => {
    expect(resolveMarketState(base)).toEqual({ state: "open", isOpen: true, reason: "weekday_hours" });
  });

  it("weekend → closed even mid-session", () => {
    expect(resolveMarketState({ ...base, weekday: 6 })).toMatchObject({ state: "closed", isOpen: false });
    expect(resolveMarketState({ ...base, weekday: 0 })).toMatchObject({ state: "closed", isOpen: false });
  });

  it("static holiday on a weekday during RTH → closed (the Juneteenth bug)", () => {
    expect(resolveMarketState({ ...base, isStaticHoliday: true })).toEqual({
      state: "closed",
      isOpen: false,
      reason: "static:holiday",
    });
  });

  it("before 09:30 / after 16:00 → closed", () => {
    expect(resolveMarketState({ ...base, minutes: 9 * 60 + 15 })).toMatchObject({ isOpen: false });
    expect(resolveMarketState({ ...base, minutes: 16 * 60 + 1 })).toMatchObject({ isOpen: false });
  });

  it("boundaries 09:30 and 16:00 inclusive → open", () => {
    expect(resolveMarketState({ ...base, minutes: 9 * 60 + 30 }).isOpen).toBe(true);
    expect(resolveMarketState({ ...base, minutes: 16 * 60 }).isOpen).toBe(true);
  });

  describe("IBKR cache is authoritative when present", () => {
    it("ibkr CLOSED overrides everything (even a weekday in RTH)", () => {
      expect(
        resolveMarketState({ ...base, ibkrDay: { status: "closed" } }),
      ).toEqual({ state: "closed", isOpen: false, reason: "ibkr:closed" });
    });

    it("ibkr open with standard 16:00 close → open during, closed after", () => {
      const day = { status: "open", open_et: "09:30", close_et: "16:00" };
      expect(resolveMarketState({ ...base, minutes: 12 * 60, ibkrDay: day }).state).toBe("open");
      expect(resolveMarketState({ ...base, minutes: 16 * 60 + 5, ibkrDay: day }).isOpen).toBe(false);
    });

    it("ibkr early-close (13:00) → early_close before, closed after", () => {
      const day = { status: "early_close", open_et: "09:30", close_et: "13:00" };
      expect(resolveMarketState({ ...base, minutes: 12 * 60, ibkrDay: day })).toEqual({
        state: "early_close",
        isOpen: true,
        reason: "ibkr:early_close",
      });
      expect(resolveMarketState({ ...base, minutes: 13 * 60 + 30, ibkrDay: day }).isOpen).toBe(false);
    });
  });
});

describe("marketState / isMarketOpen (I/O wiring against the real static holidays file)", () => {
  // 2026-06-19 is Juneteenth (a Friday) — present in scripts/config/market_holidays.json.
  // Build an instant at 13:00 ET on that date (17:00Z, EDT = UTC-4).
  const juneteenth1pmEt = new Date("2026-06-19T17:00:00Z");
  // 2026-06-18 is a normal Thursday.
  const thursday1pmEt = new Date("2026-06-18T17:00:00Z");

  it("Juneteenth 13:00 ET → market CLOSED via the holiday table", () => {
    expect(isMarketOpen(juneteenth1pmEt)).toBe(false);
    expect(marketState(juneteenth1pmEt).reason).toBe("static:holiday");
  });

  it("ordinary Thursday 13:00 ET → market OPEN", () => {
    expect(isMarketOpen(thursday1pmEt)).toBe(true);
  });
});
