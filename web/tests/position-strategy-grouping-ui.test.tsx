/** @vitest-environment jsdom */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import PositionStrategyGrouping, { type PositionStrategyGroupingControls } from "@/components/PositionStrategyGrouping";
import type { PortfolioPosition } from "@/lib/types";
import { defaultStrategyGroups, projectStrategyGroups, suggestStrategyGroups } from "@/lib/positionStrategyGroups";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function source(): PortfolioPosition {
  return {
    id: 42, account_id: "DU123", position_instance_id: "aaoi-opening-1", ticker: "AAOI",
    structure: "Combo (3 legs)", structure_type: "Combo", risk_profile: "undefined",
    expiry: "2026-11-20", contracts: 60, direction: "COMBO", entry_cost: -2386,
    market_value: -18200, max_risk: null, ib_daily_pnl: 810,
    kelly_optimal: null, target: null, stop: null, entry_date: "2026-10-13", basis_source: "ib",
    legs: [
      { con_id: 101, type: "Call", strike: 105, direction: "LONG", contracts: 60, entry_cost: 59340, avg_cost: 989, market_price: 7.2, market_value: 43200, basis_source: "ib" },
      { con_id: 102, type: "Call", strike: 140, direction: "SHORT", contracts: 60, entry_cost: 17340, avg_cost: 289, market_price: 1.9, market_value: 11400, basis_source: "ib" },
      { con_id: 103, type: "Put", strike: 90, direction: "SHORT", contracts: 50, entry_cost: 44386, avg_cost: 887.72, market_price: 10, market_value: 50000, basis_source: "ib" },
    ],
  };
}

function controls(): PositionStrategyGroupingControls {
  return {
    sourceFor: position => position,
    groupsFor: defaultStrategyGroups,
    suggestionFor: suggestStrategyGroups,
    preview: projectStrategyGroups,
    apply: vi.fn(() => true), reset: vi.fn(() => true), undo: vi.fn(() => true), canUndo: false,
  };
}

function open() {
  fireEvent.click(screen.getByRole("button", { name: "Group strategies for AAOI" }));
  return within(screen.getByRole("dialog"));
}

describe("strategy grouping interaction boundaries", () => {
  it("fits the measured popover and Reset footer above the viewport edge on open and resize", () => {
    const width = Object.getOwnPropertyDescriptor(window, "innerWidth")!;
    const height = Object.getOwnPropertyDescriptor(window, "innerHeight")!;
    const nativeRect = HTMLElement.prototype.getBoundingClientRect;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 900 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 600 });
    const rect = (left: number, top: number, width: number, height: number): DOMRect => ({
      x: left, y: top, left, top, width, height, right: left + width, bottom: top + height,
      toJSON: () => ({}),
    });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains("strategy-group-trigger")) return rect(880, 560, 40, 40);
      if (this.classList.contains("strategy-group-panel--menu")) return rect(parseFloat(this.style.left), parseFloat(this.style.top), 340, 450);
      if (this.textContent === "Reset grouping") {
        const panel = this.closest<HTMLElement>(".strategy-group-panel")!;
        return rect(parseFloat(panel.style.left), parseFloat(panel.style.top) + 410, 120, 40);
      }
      return nativeRect.call(this);
    });
    try {
      render(<PositionStrategyGrouping position={source()} controls={controls()} />);
      const dialog = open();
      const panel = screen.getByRole("dialog") as HTMLElement;
      const reset = dialog.getByRole("button", { name: "Reset grouping" });
      expect(parseFloat(panel.style.top)).toBe(138);
      expect(panel.getBoundingClientRect().right).toBeLessThanOrEqual(888);
      expect(reset.getBoundingClientRect().bottom).toBeLessThanOrEqual(588);
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 700 });
      Object.defineProperty(window, "innerHeight", { configurable: true, value: 520 });
      fireEvent(window, new Event("resize"));
      expect(parseFloat(panel.style.top)).toBe(58);
      expect(panel.getBoundingClientRect().right).toBeLessThanOrEqual(688);
      expect(reset.getBoundingClientRect().bottom).toBeLessThanOrEqual(508);
      expect(parseFloat(panel.style.top)).toBeGreaterThanOrEqual(12);
    } finally {
      Object.defineProperty(window, "innerWidth", width);
      Object.defineProperty(window, "innerHeight", height);
    }
  });

  it("closes the preview on Escape bubbling from its focused Apply button without applying", () => {
    const state = controls();
    render(<PositionStrategyGrouping position={source()} controls={state} />);
    const dialog = open();
    fireEvent.click(dialog.getByRole("button", { name: /^Suggested split/ }));
    const apply = dialog.getByRole("button", { name: "Apply grouping" });
    apply.focus();
    // Dispatch at the real focused portal descendant, never directly at window.
    fireEvent.keyDown(apply, { key: "Escape", bubbles: true });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(state.apply).not.toHaveBeenCalled();
  });

  it("lets Tab and Shift+Tab from an editor input reach the shared focus trap", () => {
    const state = controls();
    render(<PositionStrategyGrouping position={source()} controls={state} />);
    const dialog = open();
    fireEvent.click(dialog.getByRole("button", { name: /^Edit strategies/ }));
    const panel = screen.getByRole("dialog");
    // jsdom has no layout; mark the rendered controls as visible for the trap.
    for (const element of panel.querySelectorAll<HTMLElement>("button,input,select")) {
      Object.defineProperty(element, "offsetParent", { configurable: true, value: document.body });
    }
    const input = dialog.getByRole("textbox", { name: "Strategy name 1" });
    input.focus();
    fireEvent.keyDown(input, { key: "Tab", bubbles: true });
    expect(document.activeElement).toBe(dialog.getByRole("button", { name: "Add strategy" }));
    input.focus();
    fireEvent.keyDown(input, { key: "Tab", shiftKey: true, bubbles: true });
    expect(document.activeElement).toBe(dialog.getByRole("button", { name: "Close grouping" }));
    expect(state.apply).not.toHaveBeenCalled();
  });

  it("previews complete suggested financial rows and cancels without applying", () => {
    const position = source();
    const state = controls();
    const before = JSON.stringify(position);
    render(<PositionStrategyGrouping position={position} controls={state} />);
    const dialog = open();
    fireEvent.click(dialog.getByRole("button", { name: /^Suggested split/ }));
    expect(dialog.getByRole("heading", { name: "Bull Call Spread" })).toBeTruthy();
    expect(dialog.getByRole("heading", { name: "Short Put" })).toBeTruthy();
    expect(dialog.getByText("$42,000")).toBeTruthy();
    expect(dialog.getByText("-$44,386")).toBeTruthy();
    expect(dialog.getByText("-$10,200")).toBeTruthy();
    expect(dialog.getByText("-$5,614")).toBeTruthy();
    fireEvent.click(dialog.getByRole("button", { name: "Cancel", exact: true }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(state.apply).not.toHaveBeenCalled();
    expect(JSON.stringify(position)).toBe(before);
  });

  it("requires two selected legs and applies the manual partition only after preview", () => {
    const position = source();
    const state = controls();
    render(<PositionStrategyGrouping position={position} controls={state} />);
    const dialog = open();
    fireEvent.click(dialog.getByRole("button", { name: /^Select legs/ }));
    const group = dialog.getByRole("button", { name: "Group as spread" }) as HTMLButtonElement;
    expect(group.disabled).toBe(true);
    fireEvent.click(dialog.getByRole("checkbox", { name: "Select LONG 60 Call $105" }));
    expect(group.disabled).toBe(true);
    fireEvent.click(dialog.getByRole("checkbox", { name: "Select SHORT 60 Call $140" }));
    fireEvent.change(dialog.getByRole("textbox", { name: "Spread name" }), { target: { value: "Call pair" } });
    expect(group.disabled).toBe(false);
    fireEvent.click(group);
    expect(state.apply).not.toHaveBeenCalled();
    expect(dialog.getByRole("heading", { name: "Call pair" })).toBeTruthy();
    expect(dialog.getByRole("heading", { name: "Short Put" })).toBeTruthy();
    fireEvent.click(dialog.getByRole("button", { name: "Apply grouping" }));
    expect(state.apply).toHaveBeenCalledOnce();
    const groups = vi.mocked(state.apply).mock.calls[0][1];
    expect(groups.map(item => item.name)).toEqual(["Call pair", "Short Put"]);
    expect(projectStrategyGroups(position, groups)?.map(row => row.contracts)).toEqual([60, 50]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("validates named editor groups and preserves leg assignments through Back", () => {
    const position = source();
    const state = controls();
    render(<PositionStrategyGrouping position={position} controls={state} />);
    const dialog = open();
    fireEvent.click(dialog.getByRole("button", { name: /^Edit strategies/ }));
    const name = dialog.getByRole("textbox", { name: "Strategy name 1" });
    fireEvent.change(name, { target: { value: " " } });
    expect((dialog.getByRole("button", { name: "Preview grouping" }) as HTMLButtonElement).disabled).toBe(true);
    expect(state.apply).not.toHaveBeenCalled();
    fireEvent.change(name, { target: { value: "Call spread" } });
    fireEvent.click(dialog.getByRole("button", { name: "Add strategy" }));
    fireEvent.change(dialog.getByRole("textbox", { name: "Strategy name 2" }), { target: { value: "Put income" } });
    const assignment = dialog.getByRole("combobox", { name: "Strategy group for SHORT 50 Put $90" }) as HTMLSelectElement;
    const option = Array.from(assignment.options).find(item => item.text === "Put income")!;
    fireEvent.change(assignment, { target: { value: option.value } });
    fireEvent.click(dialog.getByRole("button", { name: "Preview grouping" }));
    expect(dialog.getByRole("heading", { name: "Put income" })).toBeTruthy();
    fireEvent.click(dialog.getByRole("button", { name: "Back" }));
    expect((dialog.getByRole("combobox", { name: "Strategy group for SHORT 50 Put $90" }) as HTMLSelectElement).value).toBe(option.value);
    fireEvent.click(dialog.getByRole("button", { name: "Preview grouping" }));
    fireEvent.click(dialog.getByRole("button", { name: "Apply grouping" }));
    expect(vi.mocked(state.apply).mock.calls[0][1].map(item => item.name)).toEqual(["Call spread", "Put income"]);
  });

  it("keeps an editor open through harmless leg reorder and dismisses it on changed quantities", () => {
    const position = source();
    const state = controls();
    const view = render(<PositionStrategyGrouping position={position} controls={state} />);
    const dialog = open();
    fireEvent.click(dialog.getByRole("button", { name: /^Select legs/ }));
    fireEvent.click(dialog.getByRole("checkbox", { name: "Select LONG 60 Call $105" }));
    const reordered = { ...position, legs: [...position.legs].reverse() };
    view.rerender(<PositionStrategyGrouping position={reordered} controls={state} />);
    expect((screen.getByRole("checkbox", { name: "Select LONG 60 Call $105" }) as HTMLInputElement).checked).toBe(true);
    const changed = { ...reordered, legs: reordered.legs.map(leg => ({ ...leg, contracts: leg.contracts + 1 })) };
    view.rerender(<PositionStrategyGrouping position={changed} controls={state} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(state.apply).not.toHaveBeenCalled();
  });

  it("retains the preview and shows an error when applying a stale plan fails", () => {
    const state = controls();
    state.apply = vi.fn(() => false);
    render(<PositionStrategyGrouping position={source()} controls={state} />);
    const dialog = open();
    fireEvent.click(dialog.getByRole("button", { name: /^Suggested split/ }));
    fireEvent.click(dialog.getByRole("button", { name: "Apply grouping" }));
    expect(screen.getByRole("dialog", { name: "Preview AAOI grouping" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Holdings changed");
    expect(state.apply).toHaveBeenCalledOnce();
  });

  it("keeps single legs and ambiguous contract snapshots out of the grouping controls", () => {
    const position = source();
    const state = controls();
    const view = render(<PositionStrategyGrouping position={{ ...position, legs: [position.legs[0]] }} controls={state} />);
    expect(screen.queryByRole("button", { name: "Group strategies for AAOI" })).toBeNull();
    view.rerender(<PositionStrategyGrouping position={{ ...position, legs: [position.legs[0], position.legs[0]] }} controls={state} />);
    expect(screen.queryByRole("button", { name: "Group strategies for AAOI" })).toBeNull();
    expect(state.apply).not.toHaveBeenCalled();
  });
});
