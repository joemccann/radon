/**
 * @vitest-environment jsdom
 *
 * Regime strip day change — driven through the REAL implementation.
 *
 * This file used to hold a hand-copied `computeDayChange` / `computePointChange`
 * and imported nothing from the app, so it agreed with whatever the panel did.
 * The 2026-08-28 incident (VIX rendered `-1.89 (-11.35%)` against a stale 16.65
 * baseline on a day it was +1.65%) was a BASELINE-SELECTION defect, not an
 * arithmetic one, and all nine assertions in the file named `regime-day-change`
 * passed throughout it. T-280.
 *
 * The baseline now comes from `resolveRegimeStripLiveState` and the rendering
 * from the exported `DayChange` / `PointChange` components, so the two halves
 * the panel actually composes are both under test.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { DayChange, PointChange } from "../components/RegimeStrip";
import { resolveRegimeStripLiveState } from "../lib/regimeLiveStrip";

afterEach(cleanup);

/** The session the strip anchors a day change to (previous completed close). */
const SESSION = "2026-08-27";

function quote(last: number | null, close: number | null) {
  return { last, close } as never;
}

/** Rendered text of the day-change node, or null when it renders nothing. */
function dayChangeText(): string | null {
  return screen.queryByTestId("regime-day-chg")?.textContent ?? null;
}

describe("Regime strip day change — baseline selection", () => {
  it("prices VIX against the previous session close in history, not the stale scan spot", () => {
    // 2026-08-28 incident shape: the CRI scan's spot reading AND the relay's
    // cached tick-9 close are both a stale 16.65; only history carries the
    // 2026-08-27 close of 14.52.
    const state = resolveRegimeStripLiveState({
      sessionDate: SESSION,
      prices: { VIX: quote(14.76, 16.65) },
      data: {
        vix: 16.65,
        history: [
          { date: "2026-08-26", vix: 14.3 },
          { date: "2026-08-27", vix: 14.52 },
        ],
      },
    });

    expect(state.vixClose).toBe(14.52);
    render(<DayChange last={state.liveVix} close={state.vixClose} />);
    expect(dayChangeText()).toBe("+0.24 (+1.65%)");
    // The number the incident actually shipped.
    expect(dayChangeText()).not.toBe("-1.89 (-11.35%)");
  });

  it("prices SPY against its own history close and keeps the dollar prefix", () => {
    const state = resolveRegimeStripLiveState({
      sessionDate: SESSION,
      prices: { SPY: quote(560.25, 549.0) },
      data: {
        spy: 549.0,
        history: [{ date: "2026-08-27", spy: 555.1 }],
      },
    });

    expect(state.spyClose).toBe(555.1);
    render(<DayChange last={state.liveSpy} close={state.spyClose} prefix="$" />);
    expect(dayChangeText()).toBe("$+5.15 (+0.93%)");
  });

  it("renders a negative day change from the history baseline", () => {
    const state = resolveRegimeStripLiveState({
      sessionDate: SESSION,
      prices: { VVIX: quote(110.0, 130.0) },
      data: {
        vvix: 130.0,
        history: [{ date: "2026-08-27", vvix: 120.0 }],
      },
    });

    expect(state.vvixClose).toBe(120.0);
    render(<DayChange last={state.liveVvix} close={state.vvixClose} />);
    expect(dayChangeText()).toBe("-10.00 (-8.33%)");
  });

  it("renders a flat day as +0.00", () => {
    const state = resolveRegimeStripLiveState({
      sessionDate: SESSION,
      prices: { VIX: quote(24.0, 19.0) },
      data: { vix: 19.0, history: [{ date: "2026-08-27", vix: 24.0 }] },
    });

    expect(state.vixClose).toBe(24.0);
    render(<DayChange last={state.liveVix} close={state.vixClose} />);
    expect(dayChangeText()).toBe("+0.00 (+0.00%)");
  });

  it("withholds the baseline — and the whole node — when history is too old to anchor", () => {
    // Newest row is 8 calendar days before the session: past
    // MAX_PREVIOUS_CLOSE_GAP_DAYS, so no baseline beats a wrong one (R-200).
    const state = resolveRegimeStripLiveState({
      sessionDate: SESSION,
      prices: { VIX: quote(14.76, 16.65) },
      data: { vix: 16.65, history: [{ date: "2026-08-19", vix: 21.4 }] },
    });

    expect(state.vixClose).toBeNull();
    render(<DayChange last={state.liveVix} close={state.vixClose} />);
    expect(dayChangeText()).toBeNull();
  });

  it("withholds the baseline when the payload carries no history at all", () => {
    // NOT a relay-close fallback. `prices.VIX.close` is IB's tick-9 close
    // cached in the relay's memory for the life of the process, and the guard
    // that read it fired exactly when history was empty — which is the
    // DEGRADED case, since the regime route sets `history: []` on any upstream
    // failure. R-404 removed that fallback; this case was written against the
    // behaviour it replaced and asserted the relay close (14.52) instead.
    const state = resolveRegimeStripLiveState({
      sessionDate: SESSION,
      prices: { VIX: quote(14.76, 14.52) },
      data: { vix: 16.65 },
    });

    expect(state.vixClose).toBeNull();
    render(<DayChange last={state.liveVix} close={state.vixClose} />);
    expect(dayChangeText()).toBeNull();
  });

  it("renders nothing while the market is closed and there is no live last", () => {
    const state = resolveRegimeStripLiveState({
      sessionDate: SESSION,
      marketOpen: false,
      prices: { VIX: quote(14.76, 16.65) },
      data: { vix: 14.7, history: [{ date: "2026-08-27", vix: 14.52 }] },
    });

    expect(state.liveVix).toBeNull();
    render(<DayChange last={state.liveVix} close={state.vixClose} />);
    expect(dayChangeText()).toBeNull();
  });

  it("renders nothing when the resolved baseline is not a usable close", () => {
    render(<DayChange last={25.0} close={0} />);
    expect(dayChangeText()).toBeNull();
  });
});

describe("Regime strip point change (RVOL, COR1M)", () => {
  it("renders the intraday RVOL delta with its suffix and label", () => {
    render(<PointChange change={11.52 - 11.53} suffix="%" label="intraday" />);
    expect(dayChangeText()).toBe("-0.01% intraday");
  });

  it("renders a positive point change with an explicit sign", () => {
    render(<PointChange change={6.88} suffix=" pts" />);
    expect(dayChangeText()).toBe("+6.88 pts");
  });

  it("renders nothing for a negligible change", () => {
    render(<PointChange change={0.001} suffix="%" />);
    expect(dayChangeText()).toBeNull();
  });

  it("renders nothing for a null change", () => {
    render(<PointChange change={null} suffix="%" />);
    expect(dayChangeText()).toBeNull();
  });
});
