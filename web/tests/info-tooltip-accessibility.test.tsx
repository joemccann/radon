// @vitest-environment jsdom
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import InfoTooltip from "@/components/InfoTooltip";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("InfoTooltip accessible controls", () => {
  it("has one named button, a related explanation, and Escape dismissal", () => {
    render(<InfoTooltip ariaLabel="About tokens" text="Tokens represent reported activity." prose triggerTestId="trigger" />);
    const button = screen.getByRole("button", { name: "About tokens" });
    expect(screen.getByTestId("trigger").hasAttribute("tabindex")).toBe(false);
    fireEvent.focus(button);
    const tooltip = screen.getByRole("tooltip");
    expect(button.getAttribute("aria-describedby")).toBe(tooltip.id);
    expect(tooltip.style.fontSize).toBe("12px");
    fireEvent.keyDown(button, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    expect(button.getAttribute("aria-expanded")).toBe("false");
  });
  it("allows a touch click after focus to open and a second tap to close", () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
    render(<InfoTooltip ariaLabel="About tokens" text="Tokens represent reported activity." />);
    const button = screen.getByRole("button", { name: "About tokens" });
    fireEvent.focus(button);
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.click(button);
    expect(screen.getByRole("tooltip")).toBeTruthy();
    fireEvent.click(button);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
