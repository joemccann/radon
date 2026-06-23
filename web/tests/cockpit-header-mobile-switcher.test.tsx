/**
 * @vitest-environment jsdom
 *
 * The STOCK|OPTION instrument switcher must exist on MOBILE too, not just
 * desktop (bug 2026-06-22: mobile header omitted it, so a held option position
 * locked the page to the option view with no way back to the underlying stock).
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: true, hasMounted: true }),
}));
vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn(), watchlist: [], isLoading: false }),
}));

import CockpitHeader from "../components/ticker-detail/CockpitHeader";

afterEach(cleanup);

function renderHeader(over: Record<string, unknown> = {}) {
  const onInstrumentViewChange = vi.fn();
  const props = {
    ticker: "MU",
    kind: "option" as const,
    quotePriceData: null,
    isSpreadNet: false,
    position: null,
    live: false,
    onDeckChange: vi.fn(),
    instrumentView: "position" as const,
    canSwitchInstrument: true,
    onInstrumentViewChange,
    ...over,
  };
  const result = render(<CockpitHeader {...(props as never)} />);
  return { ...result, onInstrumentViewChange };
}

describe("CockpitHeader mobile — STOCK|OPTION switcher", () => {
  it("renders the switcher inside the mobile header when switchable", () => {
    const { container } = renderHeader();
    expect(container.querySelector(".ckh--mobile")).not.toBeNull();
    const segs = Array.from(container.querySelectorAll(".ckh--mobile .ckh-instr-seg"));
    expect(segs.map((s) => s.textContent?.trim())).toEqual(["STOCK", "OPTION"]);
    // Held option position → OPTION segment is active.
    expect(container.querySelector(".ckh-instr-seg.on")?.textContent?.trim()).toBe("OPTION");
  });

  it("omits the switcher when the instrument can't be switched", () => {
    const { container } = renderHeader({ canSwitchInstrument: false });
    expect(container.querySelector(".ckh--mobile")).not.toBeNull();
    expect(container.querySelector(".ckh-instr")).toBeNull();
  });

  it("tapping STOCK requests the underlying view", () => {
    const { container, onInstrumentViewChange } = renderHeader();
    const stockSeg = container.querySelector(".ckh--mobile .ckh-instr-seg") as HTMLElement;
    fireEvent.click(stockSeg);
    expect(onInstrumentViewChange).toHaveBeenCalledWith("underlying");
  });
});
