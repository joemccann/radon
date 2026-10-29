/** @vitest-environment jsdom */
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DashboardNewsFeed from "../components/DashboardNewsFeed";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("../lib/useHeadlines", () => ({ useHeadlines: () => ({ items: [], status: "down" }) }));
vi.mock("../lib/useBookmarks", () => ({ useBookmarks: () => ({ isBookmarked: () => false, toggleBookmark: vi.fn() }) }));

const source = {
  kind: "dropbox", publisher: "TS Lombard",
  url: `/api/newsfeed/research/files/${"a".repeat(64)}.pdf`,
  documentDate: "2026-10-16", folderDate: "2026-10-16", pages: [1], figures: [],
  fileId: "id:fixture", revision: "r1", contentHash: "a".repeat(64),
};
const pdf = (index = 0) => ({
  id: `research-${index}`, title: `PDF article ${index}`, content: "Research evidence.",
  timestamp: new Date(Date.UTC(2026, 9, 16, 12, 0, index)).toISOString(),
  tags: [index % 2 ? "CREDIT" : "MACRO"], source,
});
const commentary = { id: "ordinary", title: "Commentary mentions a PDF", content: "A PDF reference is not provenance.", timestamp: "2026-10-16T13:00:00Z", tags: ["MACRO"] };
const fetchMock = vi.fn();
beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
async function load(posts: unknown[]) {
  fetchMock.mockResolvedValue({ ok: true, json: async () => posts });
  render(<DashboardNewsFeed />);
  await waitFor(() => expect(screen.queryByText("Collecting market analysis…")).toBeNull());
}
function openPdfs() { fireEvent.click(screen.getByRole("tab", { name: "PDFs", exact: true })); }

describe("PDF article feed tab", () => {
  it("keeps voting, Explain and Share in one action group when feedback opens", async () => {
    await load([pdf()]);
    const card = screen.getByTestId("news-feed-item");
    const explain = within(card).getByRole("button", { name: "Explain", exact: true });
    const share = within(card).getByRole("button", { name: "Share", exact: true });
    const vote = within(card).getByRole("button", { name: "Thumbs up" });
    const actions = explain.closest("[data-testid='newsfeed-explain']")!.parentElement;
    expect(vote.closest("[data-research-feedback]")!.parentElement).toBe(actions);
    expect(share.closest("[data-newsfeed-share]")!.parentElement).toBe(actions);

    fireEvent.click(vote);
    expect(within(actions!).getByRole("region", { name: "Thumbs up feedback" })).toBeTruthy();
    expect(explain.hasAttribute("disabled")).toBe(false);
    expect(share.hasAttribute("disabled")).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows only validated PDF articles and keeps them in Commentary", async () => {
    await load([commentary, pdf(), { ...pdf(1), source: { ...source, url: "https://untrusted.example/file.pdf" } }]);
    expect(screen.getAllByTestId("news-feed-item")).toHaveLength(2);
    openPdfs();
    expect(screen.getByRole("tab", { name: "PDFs" }).getAttribute("aria-selected")).toBe("true");
    const panel = screen.getByRole("tabpanel");
    expect(panel.id).toBe("feed-panel-pdfs");
    expect(within(panel).getAllByTestId("news-feed-item")).toHaveLength(1);
    expect(within(panel).getByText("PDF article 0")).toBeTruthy();
    expect(within(panel).queryByText(commentary.title)).toBeNull();
    expect(within(panel).getByRole("link", { name: "TS Lombard · Source PDF" }).getAttribute("href")).toBe(source.url);
    const rail = screen.getByTestId("feed-rail");
    expect(rail.textContent).toContain("PDF research");
    expect(rail.textContent).toContain("scraper");
    fireEvent.click(screen.getByRole("tab", { name: "Commentary" }));
    expect(screen.getAllByTestId("news-feed-item")).toHaveLength(2);
  });
  it("filters the full collection before pagination and resets pages when switching tabs", async () => {
    await load([commentary, ...Array.from({ length: 19 }, (_, i) => pdf(i))]);
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    openPdfs();
    expect(screen.getByText("Page 1 of 2")).toBeTruthy();
    expect(screen.getByText("Showing 1–18 of 19")).toBeTruthy();
    expect(screen.getAllByTestId("news-feed-item")).toHaveLength(18);
    fireEvent.click(within(screen.getByRole("navigation", { name: "Newsfeed pagination" })).getByRole("button", { name: "Next page" }));
    expect(screen.getAllByTestId("news-feed-item")).toHaveLength(1);
    fireEvent.click(screen.getByRole("tab", { name: "Commentary" }));
    expect(screen.getByText("Showing 1–18 of 20")).toBeTruthy();
    // Real research cards exercise Markdown, feedback and share controls too.
    // Match the existing pagination suite's bounded CI rendering allowance.
  }, 20_000);
  it("retains tag filtering and clears it within the PDF collection", async () => {
    await load([commentary, pdf(), pdf(1)]);
    openPdfs();
    fireEvent.click(screen.getByRole("button", { name: "MACRO", exact: true }));
    expect(screen.getAllByTestId("news-feed-item")).toHaveLength(1);
    expect(screen.queryByText("PDF article 1")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(screen.getAllByTestId("news-feed-item")).toHaveLength(2);
  });
  it("clears an unmatched tag within PDFs without admitting commentary", async () => {
    await load([{ ...commentary, tags: ["ONLY_COMMENTARY"] }, pdf()]);
    fireEvent.click(screen.getByRole("button", { name: "ONLY_COMMENTARY", exact: true }));
    openPdfs();
    expect(screen.getByText("No posts match the selected filter.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear filter", exact: true }));
    expect(screen.getAllByTestId("news-feed-item")).toHaveLength(1);
    expect(screen.queryByText(commentary.title)).toBeNull();
  });
  it("keeps a hidden PDF out of both collections after saving feedback", async () => {
    await load([commentary, pdf()]);
    openPdfs();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Thumbs down" }));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Save feedback" }));
    await waitFor(() => expect(screen.getByText("No PDF articles available.")).toBeTruthy());
    expect(fetchMock).toHaveBeenLastCalledWith("/api/newsfeed/research/feedback", {
      method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
      body: JSON.stringify({ postId: "research-0", vote: "down", reasons: [], comment: "" }),
    });
    fireEvent.click(screen.getByRole("tab", { name: "Commentary" }));
    expect(screen.getAllByTestId("news-feed-item")).toHaveLength(1);
    expect(screen.getByText(commentary.title)).toBeTruthy();
    expect(screen.queryByText("PDF article 0")).toBeNull();
  });
  it("refreshes the shared source while PDFs are open", async () => {
    await load([commentary, pdf()]);
    openPdfs();
    fetchMock.mockResolvedValue({ ok: true, json: async () => [commentary, pdf(1)] });
    fireEvent.click(screen.getByRole("button", { name: "Refresh feed" }));
    await waitFor(() => expect(screen.getByText("PDF article 1")).toBeTruthy());
    expect(screen.queryByText("PDF article 0")).toBeNull();
    expect(fetchMock).toHaveBeenLastCalledWith("/api/newsfeed/posts", expect.objectContaining({ cache: "no-store" }));
    fireEvent.click(screen.getByRole("tab", { name: "Commentary" }));
    expect(screen.getByText("PDF article 1")).toBeTruthy();
    expect(screen.getByText(commentary.title)).toBeTruthy();
  });
  it.each([{ posts: [] }, { posts: [commentary] }])("provides an empty PDF state without hiding the main feed: $posts.length posts", async ({ posts }) => {
    await load(posts);
    openPdfs();
    expect(screen.getByText("No PDF articles available.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Clear filter" })).toBeNull();
  });
});
