// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import GlyphRail from "../components/ticker-detail/GlyphRail";

afterEach(cleanup);

describe("Clear mobile instrument navigation", () => {
  it("shows four primary tools and a secondary disclosure instead of nine squeezed targets", () => {
    render(<GlyphRail activeDeck={null} onDeckChange={vi.fn()} includeOrder />);
    expect(screen.getAllByRole("button")).toHaveLength(5);
    for (const name of ["Chain", "Position", "News", "Trade", "More instrument tools"]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
  });

  it("keeps all reference decks reachable and closes the disclosure after selection", () => {
    const onDeckChange = vi.fn();
    render(<GlyphRail activeDeck={null} onDeckChange={onDeckChange} includeOrder />);
    fireEvent.click(screen.getByRole("button", { name: "More instrument tools" }));
    for (const name of ["Ratings", "Seasonality", "Company", "13F holdings", "Filings"]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("button", { name: "13F holdings" }));
    expect(onDeckChange).toHaveBeenCalledWith("h");
    expect(screen.queryByRole("button", { name: "Filings" })).toBeNull();
  });

  it("restores disclosure focus on Escape without changing the active deck", () => {
    const onDeckChange = vi.fn();
    render(<GlyphRail activeDeck="c" onDeckChange={onDeckChange} includeOrder />);
    const more = screen.getByRole("button", { name: "More instrument tools" });
    fireEvent.click(more);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Ratings" }));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(document.activeElement).toBe(more);
    expect(onDeckChange).not.toHaveBeenCalled();
  });

  it("preserves every desktop glyph and keyboard hint", () => {
    const { container } = render(<GlyphRail activeDeck="f" onDeckChange={vi.fn()} />);
    expect(container.querySelectorAll(".glyph")).toHaveLength(9);
    expect([...container.querySelectorAll(".glyph-k")].map((node) => node.textContent)).toEqual(["c", "p", "n", "r", "s", "i", "h", "f", ":"]);
  });
});
