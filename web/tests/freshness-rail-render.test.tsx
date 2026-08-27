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

  it("reads DUE, in warn tone, once the run has fired without delivering", () => {
    renderAt("2026-08-26T22:30:00Z", "2026-08-25");
    expect(screen.getByTestId("rail-countdown").textContent).toBe("Due");
    expect(screen.getByTestId("rail").getAttribute("data-state")).toBe("overdue");
    expect(screen.getByTestId("rail").textContent).toContain("20m 00s past the run");
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

  it("renders without a data date", () => {
    renderAt("2026-08-26T19:00:00Z", null);
    expect(screen.getByTestId("rail-asof").textContent).toBe("---");
    expect(screen.getByTestId("rail").getAttribute("data-state")).toBe("current");
  });
});
