// @vitest-environment jsdom
//
// Exploration 1a of the order-entry canvas: the ticket stops floating below the
// chain and becomes a persistent right dock, so legs, price, risk and CTA are
// readable without scrolling while the chain stays interactive.
//
// The chain must keep its own column - the dock never covers rows.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, waitFor } from "@testing-library/react";

let searchParamsString = "";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(searchParamsString),
  usePathname: () => "/MU",
  useRouter: () => ({
    replace: vi.fn(),
    push: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
}));

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));

vi.mock("../components/PriceChart", () => ({
  default: () => React.createElement("div", { "data-testid": "price-chart" }),
}));

import { chainFetch, chainWithLegs, clickCallCell, renderChain } from "./helpers/chainHarness";

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  searchParamsString = "";
  fetchMock.mockReset();
  fetchMock.mockImplementation((input) => chainFetch(input as RequestInfo | URL));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("chain docked ticket rail", () => {
  it("docks the ticket beside the chain instead of stacking it underneath", async () => {
    const builder = await chainWithLegs(() => clickCallCell(970, 0));

    const rail = document.querySelector(".chain-rail");
    expect(rail, "chain and ticket must share a rail row").toBeTruthy();

    // Both live inside the rail, as siblings - not ticket-after-chain in the page flow.
    expect(rail!.querySelector(".chain-grid-wrapper")).toBeTruthy();
    expect(rail!.contains(builder)).toBe(true);

    // The dock is the ticket itself, flagged so CSS can pin its width.
    expect(builder.classList.contains("order-builder--rail")).toBe(true);
  });

  it("gives the chain the full width when no legs are staged", async () => {
    renderChain();
    const rail = await waitFor(() => {
      const el = document.querySelector(".chain-rail");
      if (!el) throw new Error("rail not rendered");
      return el;
    });
    // No clicks: the ticket is absent, so nothing should reserve the dock column.
    expect(document.querySelector(".order-builder")).toBeNull();
    expect(rail.getAttribute("data-docked")).toBe("false");
  });

  it("marks the rail as docked once a leg is staged", async () => {
    await chainWithLegs(() => clickCallCell(970, 0));
    expect(document.querySelector(".chain-rail")?.getAttribute("data-docked")).toBe("true");
  });
});
