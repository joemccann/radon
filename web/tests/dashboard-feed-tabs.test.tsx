/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DashboardNewsFeed from "../components/DashboardNewsFeed";

const COMMENTARY_POST = {
  id: "p1",
  title: "What to watch",
  content: "Commentary body",
  href: "https://example.test/p1",
  isoTimestamp: "2026-08-29T16:00:00.000Z",
  timestamp: "2026-08-29T16:00:00.000Z",
  images: [] as string[],
  tags: ["MACRO"],
};

const RESEARCH_POST = {
  ...COMMENTARY_POST,
  id: "p-research",
  source: {
    kind: "dropbox" as const,
    publisher: "JPM",
    url: `/api/newsfeed/research/files/${"a".repeat(64)}.pdf`,
    documentDate: "2026-09-01",
    folderDate: "2026-09-01",
    pages: [1],
    figures: [] as { url: string; page: number; caption: string }[],
    fileId: "id",
    revision: "r1",
    contentHash: "c".repeat(64),
  },
};

const newsfeedState = vi.hoisted(() => ({
  posts: [] as Array<Record<string, unknown>>,
  lastUpdated: "2026-08-29T16:00:00.000Z",
}));

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
    posts: newsfeedState.posts,
    loading: false,
    refreshing: false,
    error: null,
    lastUpdated: newsfeedState.lastUpdated,
    refresh: vi.fn(),
  }),
}));

vi.mock("../components/ResearchRuleProposals", () => ({ default: () => null }));

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
  newsfeedState.posts = [{ ...COMMENTARY_POST }];
  newsfeedState.lastUpdated = "2026-08-29T16:00:00.000Z";
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  newsfeedState.posts = [{ ...COMMENTARY_POST }];
  newsfeedState.lastUpdated = "2026-08-29T16:00:00.000Z";
});

function railItems() {
  const rail = screen.getByTestId("feed-rail");
  return Array.from(rail.querySelectorAll("[data-k]")).map((el) => ({
    key: el.getAttribute("data-k"),
    label: el.querySelector(".k")?.textContent,
    value: el.querySelector(".v")?.textContent,
  }));
}

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

  it("renders a feed-local calibration rail with humanized keys", () => {
    render(<DashboardNewsFeed />);
    const rail = screen.getByTestId("feed-rail");
    expect(rail.tagName).toBe("FOOTER");
    expect(rail.getAttribute("aria-label")).toBe("Feed calibration");
    expect(railItems()).toEqual([
      { key: "source", label: "Source", value: "Market Ear" },
      { key: "capture.basis", label: "Capture basis", value: "scraper" },
      { key: "last.sample", label: "Last sample", value: expect.stringMatching(/^\d{1,2}:\d{2}:\d{2}$/) },
    ]);
    expect(rail.querySelector('[data-k="source"] .v')?.getAttribute("title")).toBe("Market Ear");
  });

  it("keeps R-463 freshness on the open tab, including Held ---", async () => {
    newsfeedState.posts = [{ ...RESEARCH_POST }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/api/newsfeed/research/held")) {
          return new Response(JSON.stringify({ items: [], pending: 0 }), { status: 200 });
        }
        return new Response("{}", { status: 200 });
      }),
    );

    render(<DashboardNewsFeed />);
    expect(railItems().find((item) => item.key === "last.sample")?.value).toMatch(/^\d{1,2}:\d{2}:\d{2}$/);
    expect(railItems().find((item) => item.key === "source")?.value).toBe("Market Ear + Research");

    fireEvent.click(screen.getByRole("tab", { name: "Headlines" }));
    expect(railItems()).toEqual([
      { key: "source", label: "Source", value: "Headlines" },
      { key: "capture.basis", label: "Capture basis", value: "hub" },
      { key: "last.sample", label: "Last sample", value: expect.stringMatching(/^\d{1,2}:\d{2}:\d{2}$/) },
    ]);

    fireEvent.click(screen.getByRole("tab", { name: "Held" }));
    expect(await screen.findByText("Nothing held is waiting for review.")).toBeTruthy();
    expect(railItems()).toEqual([
      { key: "source", label: "Source", value: "Held research" },
      { key: "capture.basis", label: "Capture basis", value: "operator review" },
      { key: "last.sample", label: "Last sample", value: "---" },
    ]);
  });
});
