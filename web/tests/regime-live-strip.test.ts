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
