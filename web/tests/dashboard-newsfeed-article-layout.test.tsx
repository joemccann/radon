/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DashboardNewsFeed from "../components/DashboardNewsFeed";
import { estimateReadMinutes } from "../lib/newsfeedReadTime";
import { formatAbsolute } from "../lib/newsfeedTime";

vi.mock("next/image", () => ({
  default: ({ src, alt, className }: { src: string; alt: string; className?: string }) =>
    React.createElement("img", { src, alt, className }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(""),
}));

const fetchMock = vi.fn();

const POST = {
  id: "p1",
  title: "What to watch",
  content: "Three things matter most from here. ".repeat(60).trim(),
  timestamp: new Date(Date.now() - 26 * 60_000).toISOString(),
  images: ["/media/p1-01.png"],
  tags: ["FRANCE", "RATES"],
};

async function renderFeed(posts: unknown[]) {
  fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => posts } as Response);
  render(React.createElement(DashboardNewsFeed));
  await waitFor(() => {
    expect(screen.queryAllByRole("listitem").length).toBeGreaterThan(0);
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  // @ts-expect-error — overriding global fetch for test
  global.fetch = fetchMock;
});

afterEach(() => {
  cleanup();
});

describe("estimateReadMinutes", () => {
  it("rounds to a one-minute floor at ~200 wpm", () => {
    expect(estimateReadMinutes("")).toBe(1);
    expect(estimateReadMinutes("one two three")).toBe(1);
    expect(estimateReadMinutes("word ".repeat(420))).toBe(2);
  });
});

describe("DashboardNewsFeed article layout", () => {
  it("shows a LIVE badge in the panel header once posts have loaded", async () => {
    await renderFeed([POST]);
    expect(document.querySelector(".news-feed-live-badge")!.textContent).toBe("LIVE");
  });

  it("renders a mono meta row: stamp · Market Ear · read time", async () => {
    await renderFeed([POST]);
    const meta = document.querySelector("[data-testid='news-feed-meta']")!;
    const cells = Array.from(meta.querySelectorAll("span")).map((s) => s.textContent);
    expect(cells).toEqual([formatAbsolute(POST.timestamp), "Market Ear", "2 min read"]);
  });

  it("places the tag strip between the meta row and the body", async () => {
    await renderFeed([POST]);
    const item = document.querySelector("li.news-feed-item")!;
    const order = Array.from(item.querySelectorAll(":scope > *")).map((n) =>
      n.getAttribute("data-testid") ?? n.className.split(" ")[0],
    );
    expect(order.indexOf("news-feed-meta")).toBeLessThan(order.indexOf("news-feed-tags"));
    expect(order.indexOf("news-feed-tags")).toBeLessThan(order.indexOf("news-feed-summary"));
  });

  it("wraps the chart in a figure with a caption crediting the source", async () => {
    await renderFeed([POST]);
    const figure = document.querySelector("figure.news-feed-figure")!;
    expect(figure.querySelector("img.news-feed-image")).not.toBeNull();
    const caption = figure.querySelector("figcaption")!;
    expect(caption.textContent).toContain(POST.title);
    expect(caption.textContent).toContain("Source · Market Ear");
  });

  it("labels the footer pill as the source link", async () => {
    await renderFeed([POST]);
    const pill = screen.getByTestId("news-feed-link-pill");
    expect(pill.textContent).toBe("Source link");
    expect(pill.getAttribute("href")).toBe("https://themarketear.com/posts/p1");
  });
});
