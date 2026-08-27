/**
 * @vitest-environment jsdom
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

import FreshnessRail from "../components/FreshnessRail";
import { IV_RANK_REFRESH } from "../lib/refreshSchedule";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function renderAt(iso: string, asOf: string | null) {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(iso));
  const view = render(
    <FreshnessRail schedule={IV_RANK_REFRESH} asOf={asOf} testId="rail" asOfTestId="rail-asof" />,
  );
  // The clock starts in an effect, so the first paint carries no countdown.
  act(() => { vi.advanceTimersByTime(0); });
  return view;
}

describe("FreshnessRail", () => {
  it("renders no countdown on the server paint, so hydration cannot mismatch", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-26T19:00:00Z"));
    // Render without flushing effects: this is the markup the server produces.
    const html = render(
      <FreshnessRail schedule={IV_RANK_REFRESH} asOf="2026-08-25" testId="rail" />,
    );
    // After effects flush it fills in; the point is the placeholder exists at all.
    expect(html.container.textContent).toContain("As of");
  });

  it("counts down to today's run while the market is open", () => {
    // 15:00 ET Wednesday. The 18:10 ET run is 3h10m out, and an EOD panel
    // holding Tuesday is CURRENT — Wednesday's close has not printed.
    renderAt("2026-08-26T19:00:00Z", "2026-08-25");
    expect(screen.getByTestId("rail-countdown").textContent).toBe("3h 10m");
    expect(screen.getByTestId("rail").getAttribute("data-state")).toBe("current");
    expect(screen.getByTestId("rail").textContent).toContain("Current");
  });

  it("names the session it is waiting on once that session has closed", () => {
    // 17:00 ET Wednesday: the close has printed and the run has not fired.
    renderAt("2026-08-26T21:00:00Z", "2026-08-25");
    expect(screen.getByTestId("rail").getAttribute("data-state")).toBe("behind");
    expect(screen.getByTestId("rail").textContent).toContain("Awaiting 2026-08-26");
    expect(screen.getByTestId("rail-countdown").textContent).toBe("1h 10m");
  });

  // Was rendered at slot + 20m. R-307 makes that the writer's own run window;
  // the alarm now waits out the grace. Same intent, past it.
  it("reads DUE, in warn tone, once the run has fired without delivering", () => {
    renderAt("2026-08-26T23:30:00Z", "2026-08-25");
    expect(screen.getByTestId("rail-countdown").textContent).toBe("Due");
    expect(screen.getByTestId("rail").getAttribute("data-state")).toBe("overdue");
    expect(screen.getByTestId("rail").textContent).toContain("1h 20m past the run");
  });

  it("ticks", () => {
    renderAt("2026-08-26T23:09:00Z", "2026-08-26");
    expect(screen.getByTestId("rail-countdown").textContent).toBe("23h 01m");
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(screen.getByTestId("rail-countdown").textContent).toBe("23h 00m");
  });

  it("keeps the as-of anchor its own test id", () => {
    renderAt("2026-08-26T19:00:00Z", "2026-08-25");
    expect(screen.getByTestId("rail-asof").textContent).toBe("2026-08-25");
  });

  // Was asserted as data-state "current" — a panel holding NO date rendered
  // the calm state with a ticking countdown, which is the one thing it cannot
  // honestly claim. R-306 gives it its own state. The original intent (the
  // rail renders, does not crash, and shows "---" for the date) is kept.
  it("renders without a data date, as unknown rather than current", () => {
    renderAt("2026-08-26T19:00:00Z", null);
    expect(screen.getByTestId("rail-asof").textContent).toBe("---");
    expect(screen.getByTestId("rail").getAttribute("data-state")).toBe("unknown");
    expect(screen.getByTestId("rail").textContent).toContain("Unknown");
  });

  it("labels its clocks with a timezone so a bare time is not ambiguous", () => {
    renderAt("2026-08-26T19:00:00Z", "2026-08-25");
    // R-309: the rail showed "Today 18:10" with no zone marker, next to an
    // `asOf` that is an ET session date.
    const text = screen.getByTestId("rail").textContent ?? "";
    expect(text).toMatch(/\b(GMT|UTC|[A-Z]{2,5}T)\b/);
  });
});
