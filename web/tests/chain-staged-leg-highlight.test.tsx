// @vitest-environment jsdom
//
// The rail's bidirectional reference: a leg staged in the ticket stays visible
// in the chain it came from, so the operator can see which contracts are in the
// order without reading the ticket back. Only the side that was staged lights
// up - staging a call must not tint the put at the same strike.

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, waitFor } from "@testing-library/react";

let searchParamsString = "";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(searchParamsString),
  usePathname: () => "/MU",
  useRouter: () => ({
    replace: vi.fn(), push: vi.fn(), prefetch: vi.fn(),
    back: vi.fn(), forward: vi.fn(), refresh: vi.fn(),
  }),
}));

vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ isWatched: () => false, toggleWatch: vi.fn() }),
}));

vi.mock("../components/PriceChart", () => ({
  default: () => React.createElement("div", { "data-testid": "price-chart" }),
}));

import { chainFetch, chainWithLegs, clickCallCell, findStrikeRow } from "./helpers/chainHarness";

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

function stagedCells(strike: number, side: "call" | "put"): number {
  return findStrikeRow(strike).querySelectorAll(`.chain-cell--staged-${side}`).length;
}

describe("staged legs highlighted in the chain", () => {
  it("tints the staged call side of the strike it came from", async () => {
    await chainWithLegs(() => clickCallCell(970, 0));
    await waitFor(() => expect(stagedCells(970, "call")).toBeGreaterThan(0));
  });

  it("leaves the put side of the same strike untouched", async () => {
    await chainWithLegs(() => clickCallCell(970, 0));
    await waitFor(() => expect(stagedCells(970, "call")).toBeGreaterThan(0));
    expect(stagedCells(970, "put")).toBe(0);
  });

  it("leaves strikes that are not in the ticket untouched", async () => {
    await chainWithLegs(() => clickCallCell(970, 0));
    await waitFor(() => expect(stagedCells(970, "call")).toBeGreaterThan(0));
    expect(stagedCells(960, "call")).toBe(0);
  });

  it("tints every staged strike of a multi-leg ticket", async () => {
    await chainWithLegs(() => {
      clickCallCell(970, 0);
      clickCallCell(960, 2);
    });
    await waitFor(() => expect(stagedCells(970, "call")).toBeGreaterThan(0));
    expect(stagedCells(960, "call")).toBeGreaterThan(0);
  });

  it("tells the operator what clicking a quote does", async () => {
    await chainWithLegs(() => clickCallCell(970, 0));
    const hint = document.querySelector(".chain-rail-hint");
    expect(hint?.textContent?.toUpperCase()).toContain("ADDS LEG TO TICKET");
  });
});
