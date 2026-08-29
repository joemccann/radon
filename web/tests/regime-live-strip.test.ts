import { describe, expect, it } from "vitest";
import { resolveRegimeStripLiveState } from "@/lib/regimeLiveStrip";

describe("resolveRegimeStripLiveState", () => {
  it("uses cached end-of-day VIX/VVIX values when market is closed", () => {
    const state = resolveRegimeStripLiveState({
      marketOpen: false,
      prices: {
        VIX: { last: 19.64, close: 18.20 } as never,
        VVIX: { last: 98.73, close: 97.00 } as never,
        SPY: { last: 708.45, close: 705.10 } as never,
        COR1M: { last: 11.27, close: 11.27 } as never,
      },
      data: {
        vix: 18.92,
        vvix: 98.73,
        spy: 707.86,
        cor1m: 11.53,
        cor1m_previous_close: 11.27,
        cor1m_5d_change: 0.65,
        vvix_vix_ratio: 5.22,
        spx_100d_ma: 681.85,
        spx_distance_pct: 3.81,
      },
    });

    expect(state.liveVix).toBeNull();
    expect(state.liveVvix).toBeNull();
    expect(state.liveSpy).toBeNull();
    expect(state.liveCor1m).toBeNull();
    expect(state.vixValue).toBe(18.92);
    expect(state.vvixValue).toBe(98.73);
    expect(state.spyValue).toBe(707.86);
    expect(state.cor1mValue).toBe(11.53);
  });

  it("uses live values intraday when market is open", () => {
    const state = resolveRegimeStripLiveState({
      marketOpen: true,
      prices: {
        VIX: { last: 19.64, close: 18.20 } as never,
        VVIX: { last: 98.73, close: 97.00 } as never,
      },
      data: {
        vix: 18.92,
        vvix: 97.13,
      },
    });

    expect(state.liveVix).toBe(19.64);
    expect(state.liveVvix).toBe(98.73);
    expect(state.vixValue).toBe(19.64);
    expect(state.vvixValue).toBe(98.73);
  });
});

/**
 * Production defect 2026-08-28 09:27 ET: the VIX tile rendered
 * "14.76  -1.89 (-11.35%)" while VIX was actually UP 1.65% on the day.
 * -1.89 off 14.76 implies a 16.65 baseline — a close from an earlier
 * session, never yesterday's. The day-change baseline must be the previous
 * SESSION's close, not a scan-time spot reading and not a relay tick-9
 * close that has been frozen in the relay's memory for weeks.
 */
describe("regime strip day-change baseline", () => {
  const history = [
    { date: "2026-08-24", vix: 15.85, vvix: 88.64, spy: 763.47, cor1m: 9.29 },
    { date: "2026-08-25", vix: 15.45, vvix: 85.67, spy: 765.91, cor1m: 9.53 },
    { date: "2026-08-26", vix: 15.21, vvix: 85.24, spy: 766.08, cor1m: 9.43 },
    { date: "2026-08-27", vix: 14.51, vvix: 84.34, spy: 768.43, cor1m: 9.09 },
    { date: "2026-08-28", vix: 14.76, vvix: 88.7, spy: 767.05, cor1m: 9.51 },
  ];

  it("prefers the previous session close over a stale relay tick-9 close", () => {
    const state = resolveRegimeStripLiveState({
      marketOpen: true,
      sessionDate: "2026-08-27",
      prices: {
        VIX: { last: 14.76, close: 16.65 } as never,
        VVIX: { last: 88.7, close: 80.9 } as never,
      },
      data: { date: "2026-08-28", vix: 14.76, vvix: 88.7, history },
    });

    expect(state.vixClose).toBe(14.51);
    expect(state.vvixClose).toBe(84.34);
    const pct = ((state.liveVix! - state.vixClose!) / state.vixClose!) * 100;
    expect(pct).toBeCloseTo(1.72, 2);
  });

  it("never uses the scan-time spot reading as a previous close", () => {
    const state = resolveRegimeStripLiveState({
      marketOpen: true,
      sessionDate: "2026-08-27",
      prices: { VIX: { last: 14.76 } as never },
      data: { date: "2026-08-28", vix: 16.65, history },
    });

    expect(state.vixClose).toBe(14.51);
  });

  it("skips today's still-forming daily bar", () => {
    const state = resolveRegimeStripLiveState({
      marketOpen: true,
      sessionDate: "2026-08-27",
      prices: { SPY: { last: 767.05 } as never },
      data: { date: "2026-08-28", spy: 767.05, history },
    });

    expect(state.spyClose).toBe(768.43);
  });

  it("withholds a baseline when the cached payload predates the session", () => {
    const state = resolveRegimeStripLiveState({
      marketOpen: true,
      sessionDate: "2026-08-27",
      prices: { VIX: { last: 14.76 } as never },
      data: {
        date: "2026-08-04",
        vix: 16.65,
        history: [
          { date: "2026-08-03", vix: 15.86, vvix: 90.81, spy: 700.1, cor1m: 5.53 },
          { date: "2026-08-04", vix: 16.65, vvix: 92.57, spy: 701.2, cor1m: 6.99 },
        ],
      },
    });

    expect(state.vixClose).toBeNull();
  });

  it("does NOT fall back to the relay close when the payload carries no history", () => {
    // This case REQUIRED the fallback. `prices.VIX.close` is IB's tick-9 close
    // cached in the relay's memory for the life of the process, which this
    // module's own docblock says can be sessions behind — and the guard was
    // `data?.history?.length`, so the fallback fired exactly when history was
    // EMPTY, which IS the degraded case (the regime route sets `history: []` on
    // any upstream failure, `missing: true` or EMPTY_CRI). The half that still
    // matters — a payload with no anchorable history yields no baseline, and
    // the strip draws nothing rather than a wrong signed percentage — is what
    // is asserted now. R-404.
    const state = resolveRegimeStripLiveState({
      marketOpen: true,
      sessionDate: "2026-08-27",
      prices: { VIX: { last: 14.76, close: 14.51 } as never },
      data: { date: "2026-08-28", vix: 14.76 },
    });

    expect(state.vixClose).toBeNull();
  });
});
