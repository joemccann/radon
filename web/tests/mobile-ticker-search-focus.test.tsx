/** @vitest-environment jsdom */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));
vi.mock("@/lib/RealtimeAuthContext", () => ({ useRealtimeAuth: () => async () => null }));
vi.mock("../components/mobile/MobileAppBar", () => ({
  default: ({ onOpenSearch }: { onOpenSearch: () => void }) =>
    <button onClick={onOpenSearch}>Open ticker search</button>,
}));
vi.mock("../components/mobile/MobileTabBar", () => ({ default: () => null }));
vi.mock("../components/mobile/MobileMoreDrawer", () => ({ default: () => null }));
import MobileShell from "../components/mobile/MobileShell";

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
  push.mockClear();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllEnvs();
  document.body.style.overflow = "";
});

function openSearch() {
  fireEvent.click(screen.getByRole("button", { name: "Open ticker search" }));
  return screen.getByRole("combobox", { name: "Search ticker" });
}

describe("mobile ticker search focus", () => {
  it("focuses inside the opening click before returning to the browser", () => {
    render(<MobileShell title="Portfolio" />);
    expect(document.activeElement).toBe(document.body);
    const input = openSearch();
    // iOS can present its keyboard only while the trusted gesture is running.
    // Advancing the former 80 ms timer here would mask the regression.
    expect(document.activeElement).toBe(input);
  });

  it("does not steal the caret after a shell rerender", () => {
    const view = render(<><MobileShell title="Portfolio" /><input aria-label="Other input" /></>);
    openSearch();
    act(() => vi.advanceTimersByTime(100));
    const other = screen.getByRole("textbox", { name: "Other input" });
    act(() => other.focus());
    view.rerender(<><MobileShell title="Positions" /><input aria-label="Other input" /></>);
    act(() => vi.advanceTimersByTime(100));
    expect(document.activeElement).toBe(other);
  });

  it("restores scroll locking on close and focuses again when reopened", () => {
    document.body.style.overflow = "auto";
    render(<MobileShell title="Portfolio" />);
    openSearch();
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.click(screen.getByRole("button", { name: "Close search" }));
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(document.body.style.overflow).toBe("auto");
    const reopenedInput = openSearch();
    expect(document.activeElement).toBe(reopenedInput);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(document.body.style.overflow).toBe("auto");
  });

  it("selects a touched result without routing a touched watchlist star", () => {
    render(<MobileShell title="Portfolio" />);
    fireEvent.change(openSearch(), { target: { value: "aapl" } });
    act(() => vi.advanceTimersByTime(250));
    const option = screen.getByRole("option");
    const touch = (type = "pointerdown") => {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperty(event, "pointerType", { value: "touch" });
      return event;
    };
    fireEvent(screen.getByTestId("star-toggle"), touch());
    fireEvent(screen.getByTestId("star-toggle"), touch("pointerup"));
    expect(push).not.toHaveBeenCalled();
    const event = touch();
    fireEvent(option, event);
    expect(event.defaultPrevented).toBe(true);
    expect(push).not.toHaveBeenCalled();
    fireEvent(option, touch("pointercancel"));
    expect(push).not.toHaveBeenCalled();
    fireEvent(option, touch());
    fireEvent(option, touch("pointerup"));
    expect(push).toHaveBeenCalledExactlyOnceWith("/AAPL");
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("preserves typed symbol selection and closes before navigation", () => {
    render(<MobileShell title="Portfolio" />);
    fireEvent.change(openSearch(), { target: { value: "aapl" } });
    act(() => vi.advanceTimersByTime(250));
    fireEvent.mouseDown(screen.getByRole("option"));
    expect(push).toHaveBeenCalledWith("/AAPL");
    expect(screen.queryByRole("combobox")).toBeNull();
  });
});
