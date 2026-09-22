/** @vitest-environment jsdom */
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardNewsFeed from "../components/DashboardNewsFeed";
import { useNewsfeedPosts } from "../lib/useNewsfeedPosts";

vi.mock("next/image", () => ({
  default: ({ src, alt, className }: { src: string; alt: string; className?: string }) =>
    React.createElement("img", { src, alt, className }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("../lib/useBookmarks", () => ({
  useBookmarks: () => ({ isBookmarked: () => false, toggleBookmark: vi.fn() }),
}));
vi.mock("../lib/useHeadlines", () => ({
  useHeadlines: () => ({ items: [], status: "live" }),
}));

const base = "/api/newsfeed/research/files/";
const chart = `${base}${"a".repeat(64)}.png`;
const pdf = `${base}${"b".repeat(64)}.pdf`;
const source = {
  kind: "dropbox" as const,
  publisher: "Synthetic Bank",
  url: pdf,
  documentDate: "2026-09-08",
  folderDate: "2026-09-08",
  pages: [1],
  figures: [{ url: chart, page: 1, caption: "Investor flows" }],
  fileId: "fixture",
  revision: "r1",
  contentHash: "b".repeat(64),
};
const markdown = [
  "## Investor flows",
  "",
  "• **Trust accounts** bought **over $14.5bn** in August.",
  "",
  "- **Investment trusts** remained buyers.",
  "  - *Retail investors* added $7.8bn.",
  "",
  "First line.  ",
  "Second line.",
  "",
  "> Demand persisted.",
  "",
  "| Period | Purchases |",
  "| --- | --- |",
  "| August | $14.5bn |",
  "",
  `[Research note](${pdf})`,
].join("\n");
const post = {
  id: "research-markdown",
  title: "Foreign investor demand",
  content: markdown,
  timestamp: "2026-09-08T16:00:00Z",
  images: [chart],
  tags: ["POSITIONING"],
  source,
};
const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({ ok: true, status: 200, headers: new Headers(), json: async () => [post] });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function HookProbe() {
  const { posts } = useNewsfeedPosts();
  return posts[0] ? <pre data-testid="raw-content">{posts[0].content}</pre> : null;
}

function expectFormattedResearch(body: HTMLElement) {
  expect(within(body).getByRole("heading", { name: "Investor flows", level: 2 })).not.toBeNull();
  expect(body.querySelector("strong")?.textContent).toBe("Trust accounts");
  expect(body.querySelector("em")?.textContent).toBe("Retail investors");
  expect(body.querySelector("ul > li > ul > li")?.textContent).toBe("Retail investors added $7.8bn.");
  expect(body.querySelector("br")).not.toBeNull();
  expect(body.querySelector("blockquote")?.textContent).toContain("Demand persisted.");
  expect(within(body).getByRole("table").getAttribute("data-sortable-exempt")).toBe("markdown");
  expect(within(body).getByRole("link", { name: "Research note" }).getAttribute("href")).toBe(pdf);
  expect(body.textContent).not.toContain("**");
  expect(body.textContent).not.toContain("##");
  expect(body.textContent).toContain("• Trust accounts");
}

describe("Dropbox research rich text", () => {
  it.each([
    { format: "object", storedSource: source },
    { format: "serialized JSON", storedSource: JSON.stringify(source) },
  ])("preserves Markdown line boundaries, nested indentation, and hard breaks with $format source", async ({ storedSource }) => {
    fetchMock.mockResolvedValue({ ok: true, headers: new Headers(), json: async () => [{ ...post, source: storedSource }] });
    render(<HookProbe />);
    expect((await screen.findByTestId("raw-content")).textContent).toBe(markdown);
  });

  it("renders the same formatted document in the feed and lightbox with its source PDF", async () => {
    render(<DashboardNewsFeed />);
    const item = await screen.findByTestId("news-feed-item");
    const feedBody = item.querySelector<HTMLElement>(".news-feed-summary")!;
    expectFormattedResearch(feedBody);
    expect(within(item).getByRole("link", { name: "Synthetic Bank · Source PDF" }).getAttribute("href")).toBe(pdf);

    fireEvent.click(within(item).getByRole("button", { name: `Open lightbox for: ${post.title}` }));
    const dialog = await screen.findByRole("dialog");
    const lightboxBody = dialog.querySelector<HTMLElement>(".newsfeed-lightbox__body")!;
    expectFormattedResearch(lightboxBody);
    expect(lightboxBody.textContent).toBe(feedBody.textContent);
    expect(within(dialog).getByRole("link", { name: "Source PDF" }).getAttribute("href")).toBe(pdf);
  });

  it("renders text-only documents without needing a chart", async () => {
    fetchMock.mockResolvedValue({ ok: true, headers: new Headers(), json: async () => [{ ...post, images: [], source: { ...source, figures: [] } }] });
    render(<DashboardNewsFeed />);
    const item = await screen.findByTestId("news-feed-item");
    expectFormattedResearch(item.querySelector<HTMLElement>(".news-feed-summary")!);
    expect(within(item).getByText(/Text-only source evidence/)).not.toBeNull();
  });

  it("removes em dashes from stored research titles, Markdown, source labels and chart captions in both views", async () => {
    fetchMock.mockResolvedValue({ ok: true, headers: new Headers(), json: async () => [{
      ...post,
      title: "Investor flows — the evidence",
      content: `${markdown}\n\nDemand &mdash; still firm. Range: 10—20%.`,
      source: { ...source, publisher: "Synthetic Bank — Research", figures: [{ url: chart, page: 1, caption: "Investor flows &#8212; August" }] },
    }] });
    render(<DashboardNewsFeed />);
    const item = await screen.findByTestId("news-feed-item");
    expectFormattedResearch(item.querySelector<HTMLElement>(".news-feed-summary")!);
    expect(item.textContent).not.toMatch(/—|&(?:mdash|#8212|#x2014);/i);
    expect(item.textContent).toContain("Range: 10 to 20%.");
    expect(within(item).getByRole("link", { name: "Synthetic Bank, Research · Source PDF" }).getAttribute("href")).toBe(pdf);
    expect(item.textContent).toContain("Investor flows, August");
    fireEvent.click(within(item).getByRole("button", { name: "Open lightbox for: Investor flows, the evidence" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).not.toMatch(/—|&(?:mdash|#8212|#x2014);/i);
    expect(dialog.textContent).toContain("Investor flows, the evidence");
    expect(dialog.textContent).toContain("Investor flows, August");
    expectFormattedResearch(dialog.querySelector<HTMLElement>(".newsfeed-lightbox__body")!);
  });

  it("retains plain text and scrape cleanup for posts without a research source", async () => {
    fetchMock.mockResolvedValue({ ok: true, headers: new Headers(), json: async () => [{ ...post, id: "market-ear-post", source: undefined, content: '\"**Literal stars** remain source text.' }] });
    render(<DashboardNewsFeed />);
    const item = await screen.findByTestId("news-feed-item");
    const body = item.querySelector("p.news-feed-summary")!;
    expect(body.textContent).toBe("**Literal stars** remain source text.");
    expect(body.querySelector("strong")).toBeNull();
  });

  it("does not turn document HTML, scripts, unsafe links, or remote images into active content", async () => {
    fetchMock.mockResolvedValue({ ok: true, headers: new Headers(), json: async () => [{ ...post, content: '<script>alert(1)</script>\n\n<img src="https://untrusted.invalid/pixel">\n\n![Remote chart](https://untrusted.invalid/chart.png)\n\n[Unsafe](javascript:alert%281%29)' }] });
    render(<DashboardNewsFeed />);
    const item = await screen.findByTestId("news-feed-item");
    const body = item.querySelector(".news-feed-summary")!;
    expect(body.querySelector("script, img")).toBeNull();
    expect(body.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(body.textContent).toContain("Remote chart");
  });
});
