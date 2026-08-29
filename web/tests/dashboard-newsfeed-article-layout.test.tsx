/**
 * @vitest-environment jsdom
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
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

  it("renders a mono meta row: stamp · read time, with no source attribution", async () => {
    await renderFeed([POST]);
    const meta = document.querySelector("[data-testid='news-feed-meta']")!;
    const cells = Array.from(meta.querySelectorAll("span")).map((s) => s.textContent);
    expect(cells).toEqual([formatAbsolute(POST.timestamp), "2 min read"]);
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

  it("wraps the chart in a figure with a caption naming the chart only", async () => {
    await renderFeed([POST]);
    const figure = document.querySelector("figure.news-feed-figure")!;
    expect(figure.querySelector("img.news-feed-image")).not.toBeNull();
    const caption = figure.querySelector("figcaption")!;
    expect(caption.textContent).toBe(`Chart · ${POST.title}`);
    expect(caption.textContent).not.toContain("Market Ear");
  });

  it("renders no source-link pill in the footer", async () => {
    await renderFeed([POST]);
    expect(screen.queryByTestId("news-feed-link-pill")).toBeNull();
    const footer = screen.getByTestId("news-feed-footer");
    expect(footer.textContent).not.toContain("Source link");
  });
});

describe("figure column alignment", () => {
  const css = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");
  const rule = (selector: string) =>
    css.match(new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`))![1];

  it("keeps the chart inside the same column as the headline and copy", () => {
    expect(rule(".news-feed-figure")).toMatch(/margin:\s*36px\s+0\s+0/);
    expect(rule(".news-feed-figure")).not.toMatch(/-40px/);
  });
});
