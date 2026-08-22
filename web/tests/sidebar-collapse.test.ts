/**
 * @vitest-environment jsdom
 */

import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import Sidebar, { SIDEBAR_COLLAPSED_STORAGE_KEY } from "../components/Sidebar";

function renderSidebar() {
  return render(
    createElement(Sidebar, {
      activeSection: "regime",
      actionTone: "#05AD98",
    }),
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe("Sidebar collapse", () => {
  it("starts expanded and collapses to an icon rail that keeps every link reachable", () => {
    const { container } = renderSidebar();
    const aside = container.querySelector("aside.sidebar")!;
    expect(aside.getAttribute("data-collapsed")).toBe("false");

    fireEvent.click(screen.getByRole("button", { name: /collapse navigation/i }));

    expect(aside.getAttribute("data-collapsed")).toBe("true");
    // Every link still has an accessible name while collapsed (icon-only rail).
    expect(screen.getByRole("link", { name: /dashboard/i })).toBeTruthy();
    expect(screen.getByRole("link", { name: /^regime$/i }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: /^cta$/i })).toBeTruthy();
    // The rail surfaces items from groups the user had folded (operations folds by default).
    expect(screen.getByRole("link", { name: /^profile$/i })).toBeTruthy();
  });

  it("expands again from the same control and persists the preference", () => {
    renderSidebar();
    const toggle = screen.getByRole("button", { name: /collapse navigation/i });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(toggle);
    expect(window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe("1");
    const expand = screen.getByRole("button", { name: /expand navigation/i });
    expect(expand.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(expand);
    expect(window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY)).toBe("0");
    expect(screen.getByRole("button", { name: /collapse navigation/i })).toBeTruthy();
  });

  it("restores a saved collapsed state after mount", () => {
    window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, "1");
    const { container } = renderSidebar();
    expect(container.querySelector("aside.sidebar")!.getAttribute("data-collapsed")).toBe("true");
  });
});
