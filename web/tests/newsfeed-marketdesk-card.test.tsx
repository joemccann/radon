/** @vitest-environment jsdom */
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardNewsFeed from "../components/DashboardNewsFeed";

vi.mock("next/image", () => ({
  default: ({ src, alt, className, unoptimized }: { src: string; alt: string; className?: string; unoptimized?: boolean }) =>
    React.createElement("img", { src, alt, className, "data-unoptimized": unoptimized ? "true" : "false" }),
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
const sha = "e".repeat(64);
const pdf = `${base}${sha}.pdf`;
const first = `${base}${"a".repeat(64)}.png`;
const second = `${base}${"b".repeat(64)}.png`;
const source = {
  kind: "marketdesk" as const,
  publisher: "MarketDesk",
  articleId: "4AJs5Adl2Yp",
  articleUrl: "https://marketdesk.ai/library/browse/4AJs5Adl2Yp",
  contentHash: sha,
  documentDate: "2026-10-26",
  folderDate: "2026-10-26",
  pages: [1],
  url: pdf,
  figures: [{ url: first, caption: "Breadth thrust" }, { url: second, caption: "Credit spreads" }],
  summary: ["Breadth improved across sectors", "Spreads — tighter on the week"],
  extraImages: [`${base}${"f".repeat(64)}.png`],
};
const post = {
  id: "research-marketdesk-4AJs5Adl2Yp",
  title: "Breadth is doing the work",
  content: "## Setup\n\n**Breadth** is broadening. Name A yields 3.5-4.0%. Name B sits at 2.1%.",
  timestamp: "2026-10-26T14:00:00Z",
  images: [first, second],
  tags: ["MARKETDESK"],
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

describe("MarketDesk research card", () => {
  it("captions figures without a page number and keeps private images unoptimized", async () => {
    render(<DashboardNewsFeed />);
    const item = await screen.findByTestId("news-feed-item");
    const caption = item.querySelector("figcaption")!;
    expect(caption.textContent).toBe("MarketDesk · Breadth thrust");
    expect(item.textContent).not.toMatch(/\bp\. \d/);
    const main = within(item).getByRole("button", { name: `Open lightbox for: ${post.title}` }).querySelector("img")!;
    expect(main.getAttribute("src")).toBe(first);
    expect(main.getAttribute("data-unoptimized")).toBe("true");
    const thumb = within(item).getByRole("button", { name: "Open chart 2: Credit spreads" });
    expect(thumb.querySelector("img")?.getAttribute("data-unoptimized")).toBe("true");
  });

  it("links the Source PDF with the document date and no page list", async () => {
    render(<DashboardNewsFeed />);
    const item = await screen.findByTestId("news-feed-item");
    const link = within(item).getByRole("link", { name: "MarketDesk · Source PDF" });
    expect(link.getAttribute("href")).toBe(pdf);
    const line = link.closest("p")!;
    expect(line.textContent).toContain("2026-10-26");
    expect(line.textContent).not.toContain("pp.");
  });

  it("renders authored content without appending raw source bullets", async () => {
    render(<DashboardNewsFeed />);
    const item = await screen.findByTestId("news-feed-item");
    const body = item.querySelector<HTMLElement>(".news-feed-summary")!;
    expect(within(body).getByRole("heading", { name: "Setup", level: 2 })).not.toBeNull();
    expect(body.querySelector("strong")?.textContent).toBe("Breadth");
    expect(within(item).queryByRole("list", { name: "Summary" })).toBeNull();
    expect(item.textContent).not.toContain("Breadth improved across sectors");
    expect(item.textContent).not.toContain("Spreads, tighter on the week");
    expect(item.textContent).not.toContain("—");
  });

  it("does not render extraImages and does not generate charts", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, headers: new Headers(), json: async () => [{ ...post, images: [], source: { ...source, figures: [] } }] });
    render(<DashboardNewsFeed />);
    const item = await screen.findByTestId("news-feed-item");
    expect(item.querySelector(`img[src="${source.extraImages[0]}"]`)).toBeNull();
    expect(item.querySelector("svg.recharts-surface, [data-testid^='planned-chart']")).toBeNull();
    expect(within(item).getByText(/Text-only source evidence/)).not.toBeNull();
  });

  it("opens the lightbox with page-free captions and no source bullet list", async () => {
    render(<DashboardNewsFeed />);
    const item = await screen.findByTestId("news-feed-item");
    fireEvent.click(within(item).getByRole("button", { name: `Open lightbox for: ${post.title}` }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("MarketDesk · Breadth thrust")).not.toBeNull();
    expect(dialog.textContent).not.toMatch(/\bp\. \d/);
    expect(within(dialog).queryByRole("list", { name: "Summary" })).toBeNull();
    expect(within(dialog).getByRole("link", { name: "Source PDF" }).getAttribute("href")).toBe(pdf);
  });
});


it("shows an article source before its PDF is available and preserves cashtags", async () => {
  const { summary: _summary, ...bareSource } = source;
  fetchMock.mockResolvedValue({ ok: true, status: 200, headers: new Headers(), json: async () => [{
    ...post, title: "$AAPL demand holds", content: "$AAPL demand holds. $NVDA orders are unchanged.",
    source: { ...bareSource, evidenceKind: "article", pages: [], url: source.articleUrl },
  }] });
  render(<DashboardNewsFeed />);
  const item = await screen.findByTestId("news-feed-item");
  expect(within(item).getByRole("link", { name: "MarketDesk · Source article" }).getAttribute("href")).toBe(source.articleUrl);
  expect(item.textContent).toContain("$AAPL demand holds. $NVDA orders are unchanged.");
  fireEvent.click(within(item).getByRole("button", { name: "Open lightbox for: $AAPL demand holds" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByRole("link", { name: "Source article" }).getAttribute("href")).toBe(source.articleUrl);
  expect(within(dialog).queryByRole("list", { name: "Summary" })).toBeNull();
});

it("uses an asynchronously attached PDF without changing the article", async () => {
  const { summary: _summary, ...bareSource } = source;
  fetchMock.mockResolvedValue({ ok: true, status: 200, headers: new Headers(), json: async () => [{
    ...post, source: { ...bareSource, evidenceKind: "article", pages: [], url: source.articleUrl, pdfUrl: pdf, pdfHash: sha },
  }] });
  render(<DashboardNewsFeed />);
  const item = await screen.findByTestId("news-feed-item");
  expect(within(item).getByRole("link", { name: "MarketDesk · Source PDF" }).getAttribute("href")).toBe(pdf);
  expect(within(item).getByRole("heading", { name: post.title })).not.toBeNull();
});
