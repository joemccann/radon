/** @vitest-environment jsdom */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import Sidebar from "../components/Sidebar";

vi.mock("@/lib/useProfile", () => ({ useProfile: () => ({ profile: null }) }));
afterEach(cleanup);
const renderNavigation = () => render(createElement(Sidebar, { activeSection: "regime", actionTone: "currentColor" }));

describe("Clear navigation disclosure replaces sidebar collapse", () => {
  it("opens on request, closes with Escape, and returns focus to its trigger", () => {
    renderNavigation();
    const trigger = screen.getByRole("button", { name: "Open all workspaces" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const regime = screen.getByRole("link", { name: "Regime" });
    regime.focus();
    fireEvent.keyDown(regime, { key: "Escape" });
    expect(screen.queryByRole("navigation", { name: "All workspaces" })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("dismisses when a workspace is chosen", () => {
    renderNavigation();
    fireEvent.click(screen.getByRole("button", { name: "Open all workspaces" }));
    fireEvent.click(screen.getByRole("link", { name: "CTA" }));
    expect(screen.queryByRole("navigation", { name: "All workspaces" })).toBeNull();
  });

  it("dismisses outside pointer input without changing the selected route", () => {
    renderNavigation();
    fireEvent.click(screen.getByRole("button", { name: "Open all workspaces" }));
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("navigation", { name: "All workspaces" })).toBeNull();
    expect(screen.getByRole("link", { name: "Risk" }).getAttribute("aria-current")).toBe("page");
  });
});
