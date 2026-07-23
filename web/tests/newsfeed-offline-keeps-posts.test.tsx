/**
 * @vitest-environment jsdom
 *
 * DashboardNewsFeed — a failed background refresh must NOT replace a held
 * post list with the error div. Error renders only when no posts are held.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act, cleanup } from "@testing-library/react";

vi.mock("next/image", () => ({
  default: (props: { src: string; alt?: string }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={typeof props.src === "string" ? props.src : ""} alt={props.alt ?? ""} />
  ),
}));
vi.mock("../lib/useBookmarks", () => ({
  useBookmarks: () => ({ isBookmarked: () => false, toggleBookmark: async () => {} }),
}));
vi.mock("../lib/useNewsfeedTagFilter", () => ({
  useNewsfeedTagFilter: () => ({
    selectedTags: new Set<string>(),
    toggleTag: () => {},
    clearTags: () => {},
  }),
}));
vi.mock("../components/NewsfeedTagBar", () => ({ default: () => null }));
vi.mock("../components/NewsfeedLightbox", () => ({ default: () => null }));
vi.mock("../components/StarToggle", () => ({ default: () => null }));

import DashboardNewsFeed from "../components/DashboardNewsFeed";

const REFRESH_INTERVAL_MS = 2 * 60 * 1000;

function postsFixture() {
  return [
    {
      id: "post-1",
      title: "Held post survives refresh failure",
      content: "body",
      timestamp: new Date(Date.now() - 60_000).toISOString(),
      images: [],
      tags: [],
    },
  ];
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("DashboardNewsFeed offline resilience", () => {
  it("keeps the held post list when a background refresh fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(postsFixture()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<DashboardNewsFeed />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("Held post survives refresh failure")).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(REFRESH_INTERVAL_MS);
    });

    expect(container.querySelector(".news-feed-error")).toBeNull();
    expect(screen.getByText("Held post survives refresh failure")).toBeTruthy();
  });

  it("still shows the error state when no posts were ever loaded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    const { container } = render(<DashboardNewsFeed />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(container.querySelector(".news-feed-error")).not.toBeNull();
  });
});
