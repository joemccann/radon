/** @vitest-environment jsdom */
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import SharePnlButton from "../components/SharePnlButton";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("anchors above the trigger and follows ancestor scrolling without dismissing checkbox interaction", () => {
  let top = 300;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(() => ({
    top, bottom: top + 20, right: 600, left: 580, width: 20, height: 20, x: 580, y: top, toJSON() {},
  }));
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(150);
  render(<div style={{ overflowX: "auto" }}><SharePnlButton data={{
    description: "Closed NVDA Call", pnl: 100, pnlPct: 20, commission: 1,
    fillPrice: 6, entryPrice: 5, exitPrice: 6, entryTime: null, exitTime: null, time: "2026-09-16",
  }} /></div>);
  const trigger = screen.getByRole("button", { name: "Share P&L" });
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Share options" });
  expect(dialog.style.getPropertyValue("--share-popover-bottom")).toBe(`${window.innerHeight - 300 + 8}px`);
  expect(dialog.style.getPropertyValue("--share-popover-right")).toBe(`${window.innerWidth - 600}px`);
  const dollars = screen.getByRole("checkbox", { name: "P&L $" });
  fireEvent.mouseDown(dollars);
  fireEvent.click(dollars);
  expect((dollars as HTMLInputElement).checked).toBe(true);
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  top = 40;
  fireEvent.scroll(document);
  expect(dialog.style.getPropertyValue("--share-popover-top")).toBe("68px");
  expect(dialog.style.getPropertyValue("--share-popover-bottom")).toBe("auto");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
});
