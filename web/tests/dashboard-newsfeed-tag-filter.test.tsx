/**
 * @vitest-environment jsdom
 */

import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DashboardNewsFeed from "../components/DashboardNewsFeed";

vi.mock("next/image", () => ({
  default: ({ src, alt }: { src: string; alt: string }) =>
    React.createElement("img", { src, alt }),
}));

const replaceMock = vi.fn();
let searchParamsString = "";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(searchParamsString),
}));

const TAGS_BY_ID: Record<string, string[]> = {
  // Composed sample so we can test AND semantics deterministically.
  // p1, p2, p3 share BTC. p1 and p2 also share vol. p3 has macro instead.
  p1: ["BTC", "crypto", "vol"],
  p2: ["BTC", "vol", "macro"],
  p3: ["BTC", "crypto", "macro"],
  p4: ["macro", "rates", "Fed"],
  p5: ["AI", "semis", "tech"],
};

function makePosts() {
  const ids = Object.keys(TAGS_BY_ID);
  return ids.map((id, i) => ({
    id,
    title: `Headline ${id}`,
    content: `Body ${id}`,
    timestamp: new Date(Date.UTC(2026, 0, 1, 0, ids.length - i, 0)).toISOString(),
    images: [`/media/${id}-01.png`],
    rawImages: [`https://themarketear.com/images/${id}.png`],
    tags: TAGS_BY_ID[id],
  }));
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  replaceMock.mockReset();
  searchParamsString = "";
  global.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
});

async function renderFeed() {
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => makePosts(),
  } as Response);
  const utils = render(React.createElement(DashboardNewsFeed));
  await waitFor(() => {
    expect(screen.queryAllByRole("listitem").length).toBeGreaterThan(0);
  });
  return utils;
}

describe("DashboardNewsFeed tag chips", () => {
  it("renders a clickable chip for each tag on every post that has tags", async () => {
    await renderFeed();

    const items = screen.getAllByRole("listitem");
    // p1 should have BTC, crypto, vol chips
    const p1 = items[0];
    expect(within(p1).getByRole("button", { name: "BTC" })).toBeTruthy();
    expect(within(p1).getByRole("button", { name: "crypto" })).toBeTruthy();
    expect(within(p1).getByRole("button", { name: "vol" })).toBeTruthy();
  });

  it("filters with AND semantics when two chips are selected", async () => {
    await renderFeed();

    const items = screen.getAllByRole("listitem");
    // Click BTC on p1 → only posts with BTC remain (p1, p2, p3)
    fireEvent.click(within(items[0]).getByRole("button", { name: "BTC" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(3);

    // Click vol on the new p1 row → AND with BTC → only p1 and p2 remain
    const updated = screen.getAllByRole("listitem");
    fireEvent.click(within(updated[0]).getByRole("button", { name: "vol" }));
    const filtered = screen.getAllByRole("listitem");
    expect(filtered).toHaveLength(2);
    const ids = filtered.map((li) => within(li).getByRole("heading", { level: 3 }).textContent);
    expect(ids).toEqual(["Headline p1", "Headline p2"]);
  });

  it("renders an active filter bar with × buttons and a 'Clear all' control when ≥1 tag is selected", async () => {
    await renderFeed();
    const items = screen.getAllByRole("listitem");
    fireEvent.click(within(items[0]).getByRole("button", { name: "BTC" }));

    const bar = screen.getByRole("region", { name: /active tag filters/i });
    expect(within(bar).getByText(/BTC/)).toBeTruthy();
    expect(within(bar).getByRole("button", { name: /clear all/i })).toBeTruthy();

    // Removing the last filter chip via × makes the bar disappear
    fireEvent.click(within(bar).getByRole("button", { name: /remove BTC/i }));
    expect(screen.queryByRole("region", { name: /active tag filters/i })).toBeNull();
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
  });

  it("Clear all empties the filter set in one click", async () => {
    await renderFeed();
    const items = screen.getAllByRole("listitem");
    fireEvent.click(within(items[0]).getByRole("button", { name: "BTC" }));
    fireEvent.click(within(screen.getAllByRole("listitem")[0]).getByRole("button", { name: "vol" }));

    const bar = screen.getByRole("region", { name: /active tag filters/i });
    fireEvent.click(within(bar).getByRole("button", { name: /clear all/i }));

    expect(screen.queryByRole("region", { name: /active tag filters/i })).toBeNull();
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
  });

  it("restores filter state from the URL on mount", async () => {
    searchParamsString = "tags=BTC,vol";
    await renderFeed();

    const bar = screen.getByRole("region", { name: /active tag filters/i });
    expect(within(bar).getByText(/BTC/)).toBeTruthy();
    expect(within(bar).getByText(/vol/)).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(2); // p1, p2
  });

  it("updates the URL via router.replace when a chip is toggled", async () => {
    await renderFeed();
    const items = screen.getAllByRole("listitem");
    fireEvent.click(within(items[0]).getByRole("button", { name: "BTC" }));

    expect(replaceMock).toHaveBeenCalled();
    const lastUrl = replaceMock.mock.calls[replaceMock.mock.calls.length - 1][0];
    expect(lastUrl).toMatch(/\/dashboard\?tags=BTC/);
  });

  it("renders a no-results state with a Clear button when the filter excludes everything", async () => {
    // Mount with an impossible URL filter (BTC AND AI — no post has both)
    searchParamsString = "tags=BTC,AI";
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => makePosts(),
    } as Response);
    render(React.createElement(DashboardNewsFeed));

    // Wait for the no-results message rather than for listitems
    await waitFor(() => {
      expect(screen.queryByText(/no posts match/i)).toBeTruthy();
    });

    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
    expect(screen.getByRole("button", { name: /clear filter/i })).toBeTruthy();
  });

  it("resets pagination to page 1 when a new filter is applied", async () => {
    // Start on page 2 of full feed (need >18 posts) — synthesize 30 posts that all share BTC
    const many = Array.from({ length: 30 }, (_, i) => ({
      id: `q${30 - i}`,
      title: `Q${30 - i}`,
      content: "x",
      timestamp: new Date(Date.UTC(2026, 0, 1, 0, 30 - i, 0)).toISOString(),
      images: [],
      rawImages: [],
      tags: i < 15 ? ["BTC", "crypto", "vol"] : ["macro", "rates", "Fed"],
    }));
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => many } as Response);
    render(React.createElement(DashboardNewsFeed));
    await waitFor(() => expect(screen.queryAllByRole("listitem").length).toBeGreaterThan(0));

    // Move to page 2
    const nav = screen.getByRole("navigation", { name: /pagination/i });
    fireEvent.click(within(nav).getByRole("button", { name: /next/i }));
    expect(within(screen.getByRole("navigation", { name: /pagination/i })).getByText(/page 2 of 2/i)).toBeTruthy();

    // Apply a filter — should reset to page 1
    const visibleItems = screen.getAllByRole("listitem");
    const macroChip = within(visibleItems[0]).queryByRole("button", { name: "macro" });
    expect(macroChip).toBeTruthy();
    fireEvent.click(macroChip!);

    const navAfter = screen.queryAllByRole("navigation", { name: /pagination/i });
    // 15 macro posts < PAGE_SIZE=18 → pagination controls hide, OR show "Page 1 of N"
    if (navAfter.length === 0) {
      // Single-page case — implicitly page 1
      expect(screen.getAllByRole("listitem").length).toBeLessThanOrEqual(18);
    } else {
      expect(within(navAfter[0]).getByText(/page 1 of/i)).toBeTruthy();
    }
  });
});
