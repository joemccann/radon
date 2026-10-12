// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { SemanticStates } from "../components/kit";

vi.mock("@/lib/ThemeContext", () => ({ useTheme: () => ({ theme: "dark", toggleTheme: vi.fn() }) }));

afterEach(cleanup);

describe("/kit sample data (plate 28)", () => {
  it("SemanticStates shows the badge states without re-printing other cards' numbers as readings", () => {
    const { container } = render(<SemanticStates />);
    expect(screen.getByText("Clear")).toBeTruthy();
    for (const repeated of ["0.84", "+2.73σ", "132ms", "99.2%"]) {
      expect(container.textContent).not.toContain(repeated);
    }
  });

  it("labels the page's numbers as sample values", async () => {
    const { default: KitPage } = await import("../app/kit/page");
    render(<KitPage />);
    expect(screen.getByTestId("kit-sample-note").textContent).toBe("Sample values. Nothing on this page is a live reading.");
  });
});
