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

function makePosts(count: number) {
  // Newest first when sorted by timestamp desc
  return Array.from({ length: count }, (_, i) => {
    const idx = count - i; // p<count> is newest, p1 is oldest
    return {
      id: `p${idx}`,
      title: `Headline ${idx}`,
      content: `Body ${idx}`,
      timestamp: new Date(Date.UTC(2026, 0, 1, 0, idx, 0)).toISOString(),
      images: [`/media/p${idx}-01.png`],
      rawImages: [`https://themarketear.com/images/p${idx}.png`],
    };
  });
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  // @ts-expect-error — overriding global fetch for test
  global.fetch = fetchMock;
});

afterEach(() => {
  cleanup();
});

function mockPostsResponse(posts: unknown[]) {
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => posts,
  } as Response);
}

async function renderFeed(posts: unknown[]) {
  mockPostsResponse(posts);
  const utils = render(React.createElement(DashboardNewsFeed));
  // Wait for the initial async load to settle
  await waitFor(() => {
    expect(screen.queryAllByRole("listitem").length).toBeGreaterThan(0);
  });
  return utils;
}

describe("DashboardNewsFeed pagination", () => {
  it("paginates 50 posts at 18 per page and renders top + bottom controls", async () => {
    await renderFeed(makePosts(50));

    // Page 1: posts p50 down through p33 (newest 18)
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(18);
    expect(within(items[0]).getByText("Headline 50")).toBeTruthy();
    expect(within(items[17]).getByText("Headline 33")).toBeTruthy();

    // Both pagination bars present (top + bottom)
    const nav = screen.getAllByRole("navigation", { name: /pagination/i });
    expect(nav).toHaveLength(2);

    // Indicator text appears in both
    for (const bar of nav) {
      expect(within(bar).getByText(/page 1 of 3/i)).toBeTruthy();
      expect(within(bar).getByText(/showing\s*1\s*[–-]\s*18\s*of\s*50/i)).toBeTruthy();
    }
  });

  it("advances to the next page when the top Next button is clicked", async () => {
    await renderFeed(makePosts(50));

    const [topBar] = screen.getAllByRole("navigation", { name: /pagination/i });
    fireEvent.click(within(topBar).getByRole("button", { name: /next/i }));

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(18);
    // Page 2 starts at p32 (post #19 newest-first)
    expect(within(items[0]).getByText("Headline 32")).toBeTruthy();

    for (const bar of screen.getAllByRole("navigation", { name: /pagination/i })) {
      expect(within(bar).getByText(/page 2 of 3/i)).toBeTruthy();
    }
  });

  it("advances when the bottom Next button is clicked and goes back via the bottom Prev button", async () => {
    await renderFeed(makePosts(50));

    const navsBefore = screen.getAllByRole("navigation", { name: /pagination/i });
    const bottomBar = navsBefore[navsBefore.length - 1];
    fireEvent.click(within(bottomBar).getByRole("button", { name: /next/i }));

    expect(
      within(screen.getAllByRole("navigation", { name: /pagination/i })[0]).getByText(/page 2 of 3/i),
    ).toBeTruthy();

    const bottomBar2 =
      screen.getAllByRole("navigation", { name: /pagination/i }).slice(-1)[0];
    fireEvent.click(within(bottomBar2).getByRole("button", { name: /prev/i }));

    expect(
      within(screen.getAllByRole("navigation", { name: /pagination/i })[0]).getByText(/page 1 of 3/i),
    ).toBeTruthy();
  });

  it("disables Prev on the first page and Next on the last page", async () => {
    await renderFeed(makePosts(50));

    for (const bar of screen.getAllByRole("navigation", { name: /pagination/i })) {
      expect(within(bar).getByRole("button", { name: /prev/i })).toHaveProperty(
        "disabled",
        true,
      );
      expect(within(bar).getByRole("button", { name: /next/i })).toHaveProperty(
        "disabled",
        false,
      );
    }

    // Click forward to last page (page 3)
    const top = screen.getAllByRole("navigation", { name: /pagination/i })[0];
    fireEvent.click(within(top).getByRole("button", { name: /next/i }));
    fireEvent.click(
      within(screen.getAllByRole("navigation", { name: /pagination/i })[0]).getByRole(
        "button",
        { name: /next/i },
      ),
    );

    for (const bar of screen.getAllByRole("navigation", { name: /pagination/i })) {
      expect(within(bar).getByRole("button", { name: /prev/i })).toHaveProperty(
        "disabled",
        false,
      );
      expect(within(bar).getByRole("button", { name: /next/i })).toHaveProperty(
        "disabled",
        true,
      );
    }

    // Last page renders the remaining posts (50 - 36 = 14)
    expect(screen.getAllByRole("listitem")).toHaveLength(14);
  });

  it("hides pagination controls when posts fit on a single page", async () => {
    await renderFeed(makePosts(10));
    expect(screen.getAllByRole("listitem")).toHaveLength(10);
    expect(screen.queryAllByRole("navigation", { name: /pagination/i })).toHaveLength(0);
  });

  it("clamps current page when a refresh shrinks the dataset", async () => {
    // Initial render: 50 posts → 3 pages
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => makePosts(50),
    } as Response);
    render(React.createElement(DashboardNewsFeed));

    await waitFor(() => {
      expect(screen.queryAllByRole("listitem").length).toBeGreaterThan(0);
    });

    // Move to page 3
    const top = screen.getAllByRole("navigation", { name: /pagination/i })[0];
    fireEvent.click(within(top).getByRole("button", { name: /next/i }));
    fireEvent.click(
      within(screen.getAllByRole("navigation", { name: /pagination/i })[0]).getByRole(
        "button",
        { name: /next/i },
      ),
    );
    expect(
      within(screen.getAllByRole("navigation", { name: /pagination/i })[0]).getByText(/page 3 of 3/i),
    ).toBeTruthy();

    // Refresh: only 10 posts now → 1 page; current page 3 should clamp to 1
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => makePosts(10),
    } as Response);

    const refreshButton = screen.getByRole("button", { name: /refresh/i });
    await act(async () => {
      fireEvent.click(refreshButton);
    });

    await waitFor(() => {
      expect(screen.getAllByRole("listitem")).toHaveLength(10);
    });
    expect(screen.queryAllByRole("navigation", { name: /pagination/i })).toHaveLength(0);
  });
});
