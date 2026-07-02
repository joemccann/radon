/**
 * @vitest-environment jsdom
 *
 * Regression (2026-07-06): the NYSE BREADTH header tooltip clipped off the
 * top of the viewport. The flip heuristic was a fixed 120px threshold, but
 * a long tooltip renders ~270px tall — a trigger 180px from the top passed
 * the check and opened upward into the clip. The popup's real height must
 * drive the flip.
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import InfoTooltip from "../components/InfoTooltip";

const POPUP_HEIGHT = 250;

function mockRects(triggerTop: number) {
  const triggerRect = {
    top: triggerTop,
    bottom: triggerTop + 13,
    left: 400,
    right: 413,
    width: 13,
    height: 13,
    x: 400,
    y: triggerTop,
    toJSON: () => ({}),
  } as DOMRect;
  const popupRect = { ...triggerRect, height: POPUP_HEIGHT } as DOMRect;
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    return (this as HTMLElement).dataset?.testid === "tt-content" ? popupRect : triggerRect;
  });
}

function openTooltip() {
  render(
    <InfoTooltip
      text="A long explanation that renders far taller than the old 120px flip threshold."
      triggerTestId="tt-trigger"
      contentTestId="tt-content"
    />,
  );
  fireEvent.mouseEnter(screen.getByTestId("tt-trigger"));
  return screen.getByTestId("tt-content");
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("InfoTooltip flip", () => {
  it("flips below when the measured popup would not fit above the trigger", () => {
    mockRects(180);

    const popup = openTooltip();

    expect(popup.style.top).toBe(`${180 + 13 + 6}px`);
    expect(popup.style.transform).not.toContain("translateY(-100%)");
  });

  it("renders above when there is room for the measured popup", () => {
    mockRects(600);

    const popup = openTooltip();

    expect(popup.style.top).toBe(`${600 - 6}px`);
    expect(popup.style.transform).toContain("translateY(-100%)");
  });

  it("is visible once measured", () => {
    mockRects(180);

    const popup = openTooltip();

    expect(popup.style.visibility).not.toBe("hidden");
  });
});
