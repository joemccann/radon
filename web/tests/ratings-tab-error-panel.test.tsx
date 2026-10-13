// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import RatingsTab from "@/components/ticker-detail/RatingsTab";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RatingsTab error panel", () => {
  it("says in place what failed and offers one retry that refetches the ratings route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ error: "Analyst ratings unavailable from IB / Unusual Whales right now." }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<RatingsTab ticker="SPY" active />);

    expect(await screen.findByText("Analyst ratings for SPY could not be loaded")).toBeTruthy();
    expect(screen.queryByText("Reload data")).toBeNull();
    const retries = screen.getAllByRole("button", { name: /try again|retry/i });
    expect(retries).toHaveLength(1);
    // Retry shares the centered empty-state axis with the heading.
    expect(retries[0].closest(".section-empty-state")).not.toBeNull();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(retries[0]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/ticker/ratings?ticker=SPY");
  });
});
