// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import RatingsTab from "@/components/ticker-detail/RatingsTab";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stub(source: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ticker: "META",
        source,
        recommendation: "buy",
        analyst_count: 66,
        ratings: { buy: 60, hold: 6, sell: 0, total: 66, buy_pct: 90.9, sell_pct: 0 },
        recent_changes: [],
      }),
    }),
  );
}

describe("RatingsTab source label", () => {
  it("names Robinhood for rh-served consensus", async () => {
    stub("rh");
    render(<RatingsTab ticker="META" active />);
    expect(await screen.findByText("via Robinhood")).toBeTruthy();
  });

  it("still names Unusual Whales for uw", async () => {
    stub("uw");
    render(<RatingsTab ticker="META" active />);
    expect(await screen.findByText("via Unusual Whales")).toBeTruthy();
  });
});
