/**
 * @vitest-environment jsdom
 */

import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import Sidebar from "../components/Sidebar";

afterEach(() => {
  cleanup();
});

describe("Sidebar navigation", () => {
  it("hides nav items marked hidden while keeping the rest of the sidebar visible", () => {
    render(
      createElement(Sidebar, {
        activeSection: "portfolio",
        actionTone: "#05AD98",
        ibConnected: false,
        lastSync: null,
      }),
    );

    expect(screen.getByRole("link", { name: /dashboard/i })).toBeTruthy();
    expect(screen.getByRole("link", { name: /portfolio/i })).toBeTruthy();
    // Performance un-shelved with the GIPS TWR panel (lib/data.ts hidden: false).
    expect(screen.getByRole("link", { name: /performance/i })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /discover/i })).toBeNull();
  });
});
