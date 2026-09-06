/**
 * @vitest-environment jsdom
 */

import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import Sidebar from "../components/Sidebar";
import { navItems } from "../lib/data";

afterEach(() => {
  cleanup();
});

describe("Clear workspace navigation", () => {
  it("keeps four primary tasks visible and every existing workspace accessible in More", () => {
    render(
      createElement(Sidebar, {
        activeSection: "portfolio",
        actionTone: "#05AD98",
        ibConnected: false,
        lastSync: null,
      }),
    );

    const primary = within(screen.getByRole("navigation", { name: "Primary navigation" }));
    expect(primary.getAllByRole("link").map((link) => link.textContent)).toEqual(["Portfolio", "Research", "Risk", "Positions"]);
    expect(primary.getByRole("link", { name: "Portfolio" }).getAttribute("href")).toBe("/dashboard");
    expect(primary.getByRole("link", { name: "Positions" }).getAttribute("href")).toBe("/portfolio");
    expect(primary.getByRole("link", { name: "Positions" }).getAttribute("aria-current")).toBe("page");
    fireEvent.click(screen.getByRole("button", { name: "Open all workspaces" }));
    const overflow = within(screen.getByRole("navigation", { name: "All workspaces" }));
    for (const item of navItems) {
      expect(overflow.getByRole("link", { name: item.label, exact: true }).getAttribute("href")).toBe(item.href);
    }
  });
});
