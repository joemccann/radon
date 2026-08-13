// @vitest-environment jsdom
//
// ApprovalGate — human-in-the-loop confirmation primitive (web/components/agent).
//
// This is the F7 chokepoint: the gate NEVER auto-executes. It surfaces handling
// options as a radiogroup, tracks exactly one selection, and hands that option's
// id to onConfirm. ChatPanel routes a proposed order through it, so a regression
// that confirms the wrong option (or confirms while busy) routes the wrong order.
//
// Invariants pinned here:
//   1. First option is selected by default; defaultOptionId overrides.
//   2. Clicking an option moves the selection (aria-checked follows).
//   3. onConfirm receives the SELECTED option id, not the first / not the label.
//   4. busy disables confirm, dismiss, and option selection.
//   5. Dismiss never calls onConfirm.

import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import ApprovalGate, { type GateOption } from "../components/agent/ApprovalGate";

const OPTIONS: GateOption[] = [
  { id: "route", label: "Route as proposed", meta: "AS PROPOSED" },
  { id: "halve", label: "Route at half size", meta: "0.5x CLIP" },
  { id: "hold", label: "Hold for review", meta: "NO ROUTE" },
];

function renderGate(overrides: Partial<React.ComponentProps<typeof ApprovalGate>> = {}) {
  const onConfirm = vi.fn();
  const onDismiss = vi.fn();
  const utils = render(
    <ApprovalGate
      body="BUY 10 MU 2026-09-18 $120C @ 4.20 debit."
      options={OPTIONS}
      onConfirm={onConfirm}
      onDismiss={onDismiss}
      {...overrides}
    />,
  );
  return { ...utils, onConfirm, onDismiss };
}

afterEach(() => {
  cleanup();
});

describe("ApprovalGate — option selection", () => {
  it("selects the first option by default", () => {
    renderGate();
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect(radios[0].getAttribute("aria-checked")).toBe("true");
    expect(radios[1].getAttribute("aria-checked")).toBe("false");
    expect(radios[2].getAttribute("aria-checked")).toBe("false");
  });

  it("honours defaultOptionId over first-option fallback", () => {
    renderGate({ defaultOptionId: "hold" });
    const radios = screen.getAllByRole("radio");
    expect(radios[0].getAttribute("aria-checked")).toBe("false");
    expect(radios[2].getAttribute("aria-checked")).toBe("true");
  });

  it("moves the selection when another option is clicked", () => {
    renderGate();
    fireEvent.click(screen.getByText("Route at half size"));
    const radios = screen.getAllByRole("radio");
    expect(radios[0].getAttribute("aria-checked")).toBe("false");
    expect(radios[1].getAttribute("aria-checked")).toBe("true");
  });

  it("renders the declarative body statement and per-option meta", () => {
    renderGate();
    expect(screen.getByText("BUY 10 MU 2026-09-18 $120C @ 4.20 debit.")).toBeTruthy();
    expect(screen.getByText("AS PROPOSED")).toBeTruthy();
    expect(screen.getByText("NO ROUTE")).toBeTruthy();
  });
});

describe("ApprovalGate — onConfirm(optionId)", () => {
  it("confirms the default option id when nothing is clicked", () => {
    const { onConfirm } = renderGate();
    fireEvent.click(screen.getByText("Confirm selection"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith("route");
  });

  it("confirms the NEWLY selected option id, not the default", () => {
    const { onConfirm } = renderGate();
    fireEvent.click(screen.getByText("Hold for review"));
    fireEvent.click(screen.getByText("Confirm selection"));
    expect(onConfirm).toHaveBeenCalledWith("hold");
  });

  it("never fires onConfirm when dismissed", () => {
    const { onConfirm, onDismiss } = renderGate();
    fireEvent.click(screen.getByText("Dismiss"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});

describe("ApprovalGate — busy state", () => {
  it("disables confirm, dismiss and option selection while routing", () => {
    const { onConfirm, onDismiss } = renderGate({ busy: true });

    const confirm = screen.getByText("Routing…") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect((screen.getByText("Dismiss") as HTMLButtonElement).disabled).toBe(true);
    for (const radio of screen.getAllByRole("radio")) {
      expect((radio as HTMLButtonElement).disabled).toBe(true);
    }

    fireEvent.click(confirm);
    fireEvent.click(screen.getByText("Dismiss"));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("shows the settled confirm label when not busy", () => {
    renderGate();
    expect(screen.getByText("Confirm selection")).toBeTruthy();
    expect(screen.queryByText("Routing…")).toBeNull();
  });
});
