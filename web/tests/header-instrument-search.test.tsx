// @vitest-environment jsdom
import React, { forwardRef } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Header from "@/components/Header";

const { navigateToTicker } = vi.hoisted(() => ({ navigateToTicker: vi.fn() }));
vi.mock("@/lib/useTickerNav", () => ({ useTickerNav: () => ({ navigateToTicker }) }));
vi.mock("@/lib/IBStatusContext", () => ({ useIBStatusContext: () => ({ displayStatus: "connected" }) }));
vi.mock("@/components/TickerSearch", () => ({
  default: forwardRef<HTMLInputElement, { onSelect: (symbol: string) => void; ariaLabel: string }>(
    function Search({ onSelect, ariaLabel }, ref) {
      return <input ref={ref} aria-label={ariaLabel} onKeyDown={(event) => {
        if (event.key === "Enter") onSelect(event.currentTarget.value);
      }} />;
    },
  ),
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("header instrument search after palette removal", () => {
  it.each(["metaKey", "ctrlKey"])("focuses search with %s+K and still navigates", (modifier) => {
    const { unmount } = render(<Header activeLabel="Portfolio" isFullscreen={false}
      onToggleFullscreen={vi.fn()} onToggleTheme={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /command palette/i })).toBeNull();
    const search = screen.getByRole("textbox", { name: "Search ticker" });
    fireEvent.keyDown(document, { key: "k", [modifier]: true });
    expect(document.activeElement).toBe(search);
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.change(search, { target: { value: "DXCM" } });
    fireEvent.keyDown(search, { key: "Enter" });
    expect(navigateToTicker).toHaveBeenCalledWith("DXCM");
    unmount();
    const event = new KeyboardEvent("keydown", { key: "k", [modifier]: true, cancelable: true });
    document.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });
});
