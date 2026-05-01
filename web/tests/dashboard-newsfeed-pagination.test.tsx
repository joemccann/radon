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

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(""),
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
  it("paginates 50 posts at 18 per page and renders only a bottom-of-list control", async () => {
    await renderFeed(makePosts(50));

    // Page 1: posts p50 down through p33 (newest 18)
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(18);
    expect(within(items[0]).getByText("Headline 50")).toBeTruthy();
    expect(within(items[17]).getByText("Headline 33")).toBeTruthy();

    // Exactly one pagination bar — bottom only
    const nav = screen.getAllByRole("navigation", { name: /pagination/i });
    expect(nav).toHaveLength(1);

    expect(within(nav[0]).getByText(/page 1 of 3/i)).toBeTruthy();
    expect(within(nav[0]).getByText(/showing\s*1\s*[–-]\s*18\s*of\s*50/i)).toBeTruthy();
  });

  it("advances to the next page when Next is clicked", async () => {
    await renderFeed(makePosts(50));

    const bar = screen.getByRole("navigation", { name: /pagination/i });
    fireEvent.click(within(bar).getByRole("button", { name: /next/i }));

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(18);
    // Page 2 starts at p32 (post #19 newest-first)
    expect(within(items[0]).getByText("Headline 32")).toBeTruthy();
    expect(within(screen.getByRole("navigation", { name: /pagination/i })).getByText(/page 2 of 3/i)).toBeTruthy();
  });

  it("Prev returns to the previous page", async () => {
    await renderFeed(makePosts(50));

    const bar = screen.getByRole("navigation", { name: /pagination/i });
    fireEvent.click(within(bar).getByRole("button", { name: /next/i }));
    expect(within(screen.getByRole("navigation", { name: /pagination/i })).getByText(/page 2 of 3/i)).toBeTruthy();

    fireEvent.click(within(screen.getByRole("navigation", { name: /pagination/i })).getByRole("button", { name: /prev/i }));
    expect(within(screen.getByRole("navigation", { name: /pagination/i })).getByText(/page 1 of 3/i)).toBeTruthy();
  });

  it("disables Prev on the first page and Next on the last page", async () => {
    await renderFeed(makePosts(50));

    let bar = screen.getByRole("navigation", { name: /pagination/i });
    expect(within(bar).getByRole("button", { name: /prev/i })).toHaveProperty("disabled", true);
    expect(within(bar).getByRole("button", { name: /next/i })).toHaveProperty("disabled", false);

    // Click forward to last page (page 3)
    fireEvent.click(within(bar).getByRole("button", { name: /next/i }));
    bar = screen.getByRole("navigation", { name: /pagination/i });
    fireEvent.click(within(bar).getByRole("button", { name: /next/i }));

    bar = screen.getByRole("navigation", { name: /pagination/i });
    expect(within(bar).getByRole("button", { name: /prev/i })).toHaveProperty("disabled", false);
    expect(within(bar).getByRole("button", { name: /next/i })).toHaveProperty("disabled", true);

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
    let bar = screen.getByRole("navigation", { name: /pagination/i });
    fireEvent.click(within(bar).getByRole("button", { name: /next/i }));
    bar = screen.getByRole("navigation", { name: /pagination/i });
    fireEvent.click(within(bar).getByRole("button", { name: /next/i }));
    expect(within(screen.getByRole("navigation", { name: /pagination/i })).getByText(/page 3 of 3/i)).toBeTruthy();

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
