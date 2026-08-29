/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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

vi.mock("../lib/useNewsfeedPosts", () => ({
  useNewsfeedPosts: () => ({
    posts: [
      {
        id: "p1",
        title: "What to watch",
        content: "Commentary body",
        href: "https://example.test/p1",
        isoTimestamp: "2026-08-29T16:00:00.000Z",
        timestamp: "2026-08-29T16:00:00.000Z",
        images: [],
        tags: ["MACRO"],
      },
    ],
    loading: false,
    refreshing: false,
    error: null,
    lastUpdated: "2026-08-29T16:00:00.000Z",
    refresh: vi.fn(),
  }),
}));

vi.mock("../lib/useHeadlines", () => ({
  useHeadlines: () => ({
    items: [
      {
        kind: "headline",
        id: "h1",
        time: "2026-08-29T20:35:56.000Z",
        important: true,
        content: "Explosions heard in Kyiv.",
        impact: [{ symbol: "WTI", impact: "bearish" }],
      },
    ],
    status: "live",
  }),
}));

afterEach(() => {
  cleanup();
});

describe("dashboard feed tabs", () => {
  it("defaults to Commentary as the first selected tab", () => {
    render(<DashboardNewsFeed />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Commentary", "Headlines"]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(tabs[1].getAttribute("aria-selected")).toBe("false");
    expect(screen.getByText("What to watch")).toBeTruthy();
    expect(screen.queryByTestId("headlines-tape")).toBeNull();
  });

  it("shows the headlines tape on the second tab", () => {
    render(<DashboardNewsFeed />);
    fireEvent.click(screen.getByRole("tab", { name: "Headlines" }));
    expect(screen.getByRole("tab", { name: "Headlines" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("headlines-tape").textContent).toContain("Explosions heard in Kyiv.");
    expect(screen.queryByText("What to watch")).toBeNull();
  });
});
