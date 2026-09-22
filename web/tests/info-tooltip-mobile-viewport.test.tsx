/**
 * @vitest-environment jsdom
 *
 * Regression (2026-09-17): on an iPhone the Vol/Skew MR section tooltip
 * opened upward from a trigger ~190px down the page, and its 260px-wide,
 * ~450px-tall box ran off the top of the screen — the first line sat under
 * the status bar / Dynamic Island and the notch inset was ignored entirely.
 * The popup must stay inside the safe viewport on both axes, and scroll
 * rather than clip when the copy is taller than the space available.
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import InfoTooltip from "../components/InfoTooltip";

const MARGIN = 8;

function setViewport(width: number, height: number) {
  Object.defineProperty(window, "innerWidth", { value: width, writable: true, configurable: true });
  Object.defineProperty(window, "innerHeight", { value: height, writable: true, configurable: true });
}

function mockRects(triggerTop: number, triggerLeft: number, popupHeight: number) {
  const triggerRect = {
    top: triggerTop,
    bottom: triggerTop + 13,
    left: triggerLeft,
    right: triggerLeft + 13,
    width: 13,
    height: 13,
    x: triggerLeft,
    y: triggerTop,
    toJSON: () => ({}),
  } as DOMRect;
  const popupRect = { ...triggerRect, height: popupHeight } as DOMRect;
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    return (this as HTMLElement).dataset?.testid === "tt-content" ? popupRect : triggerRect;
  });
}

function openTooltip() {
  render(
    <InfoTooltip
      text="Short-term top and bottom framing from vol and skew versus spot extension."
      triggerTestId="tt-trigger"
      contentTestId="tt-content"
    />,
  );
  fireEvent.mouseEnter(screen.getByTestId("tt-trigger"));
  return screen.getByTestId("tt-content");
}

// The popup may be placed by `top` alone or pulled up by a translate, so
// assert the geometry the reader actually sees, not the mechanism.
function box(popup: HTMLElement, popupHeight = 0) {
  const raw = Number.parseFloat(popup.style.top);
  const pulledUp = popup.style.transform.includes("translateY(-100%)");
  return {
    top: pulledUp ? raw - popupHeight : raw,
    left: Number.parseFloat(popup.style.left),
    maxHeight: Number.parseFloat(popup.style.maxHeight),
    width: popup.style.width,
  };
}

beforeEach(() => setViewport(390, 844));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("InfoTooltip on a phone viewport", () => {
  it("never opens above the top of the safe viewport", () => {
    mockRects(190, 60, 450);

    const { top } = box(openTooltip(), 450);

    expect(top).toBeGreaterThanOrEqual(MARGIN);
  });

  it("clears the notch inset even when the box fits above in raw pixels", () => {
    document.documentElement.style.setProperty("--safe-top", "59px");
    mockRects(190, 60, 150);

    const { top } = box(openTooltip(), 150);

    expect(top).toBeGreaterThanOrEqual(59 + MARGIN);
    document.documentElement.style.removeProperty("--safe-top");
  });

  it("keeps the whole box inside the viewport height", () => {
    mockRects(190, 60, 450);
    const popup = openTooltip();
    const { top, maxHeight } = box(popup, 450);

    expect(top + Math.min(450, maxHeight)).toBeLessThanOrEqual(844 - MARGIN);
  });

  it("scrolls instead of clipping when the copy outgrows the viewport", () => {
    mockRects(300, 60, 1200);
    const popup = openTooltip();

    expect(box(popup).maxHeight).toBeLessThanOrEqual(844 - MARGIN * 2);
    expect(popup.style.overflowY).toBe("auto");
  });

  it("narrows to the viewport rather than overflowing a small screen", () => {
    setViewport(260, 640);
    mockRects(190, 20, 200);
    const { left, width } = box(openTooltip(), 200);

    expect(width).toBe("244px");
    expect(left).toBe(MARGIN);
  });

  it("stays anchored under its trigger when there is room below", () => {
    mockRects(40, 200, 120);

    const { top } = box(openTooltip(), 120);

    expect(top).toBe(40 + 13 + 6);
  });
});
