/**
 * @vitest-environment jsdom
 *
 * Regression (2026-08-22): every InfoTooltip across the app vanished the
 * instant the pointer left the "?" trigger, so the explanation copy could
 * never be hovered, let alone selected. The popup must accept pointer
 * events, allow text selection, and survive the short pointer trip across
 * the gap between trigger and popup; leaving both closes it after a grace
 * period, and re-entering during that period cancels the close.
 */

import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import InfoTooltip, { TOOLTIP_HIDE_DELAY_MS } from "../components/InfoTooltip";

function openTooltip() {
  render(
    <InfoTooltip
      text="Selectable explanation copy."
      triggerTestId="tt-trigger"
      contentTestId="tt-content"
    />,
  );
  fireEvent.mouseEnter(screen.getByTestId("tt-trigger"));
  return screen.getByTestId("tt-content");
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("InfoTooltip hover grace", () => {
  it("declares a non-trivial grace period", () => {
    expect(TOOLTIP_HIDE_DELAY_MS).toBeGreaterThanOrEqual(200);
  });

  it("stays open immediately after the pointer leaves the trigger", () => {
    openTooltip();
    fireEvent.mouseLeave(screen.getByTestId("tt-trigger"));
    expect(screen.queryByTestId("tt-content")).not.toBeNull();
  });

  it("closes only after the grace period elapses", () => {
    openTooltip();
    fireEvent.mouseLeave(screen.getByTestId("tt-trigger"));
    act(() => {
      vi.advanceTimersByTime(TOOLTIP_HIDE_DELAY_MS - 1);
    });
    expect(screen.queryByTestId("tt-content")).not.toBeNull();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(screen.queryByTestId("tt-content")).toBeNull();
  });

  it("re-entering the trigger during the grace period cancels the close", () => {
    openTooltip();
    const trigger = screen.getByTestId("tt-trigger");
    fireEvent.mouseLeave(trigger);
    act(() => {
      vi.advanceTimersByTime(TOOLTIP_HIDE_DELAY_MS / 2);
    });
    fireEvent.mouseEnter(trigger);
    act(() => {
      vi.advanceTimersByTime(TOOLTIP_HIDE_DELAY_MS * 2);
    });
    expect(screen.queryByTestId("tt-content")).not.toBeNull();
  });

  it("the popup itself accepts pointer events and text selection", () => {
    const popup = openTooltip();
    expect(popup.style.pointerEvents).not.toBe("none");
    expect(popup.style.userSelect).toBe("text");
  });

  it("hovering the popup keeps it open; leaving the popup closes it after the grace period", () => {
    const popup = openTooltip();
    fireEvent.mouseLeave(screen.getByTestId("tt-trigger"));
    fireEvent.mouseEnter(popup);
    act(() => {
      vi.advanceTimersByTime(TOOLTIP_HIDE_DELAY_MS * 2);
    });
    expect(screen.queryByTestId("tt-content")).not.toBeNull();

    fireEvent.mouseLeave(popup);
    act(() => {
      vi.advanceTimersByTime(TOOLTIP_HIDE_DELAY_MS);
    });
    expect(screen.queryByTestId("tt-content")).toBeNull();
  });

  it("unmounting during the grace period does not fire a stale timer", () => {
    openTooltip();
    fireEvent.mouseLeave(screen.getByTestId("tt-trigger"));
    cleanup();
    expect(() => {
      act(() => {
        vi.advanceTimersByTime(TOOLTIP_HIDE_DELAY_MS);
      });
    }).not.toThrow();
  });
});
