/**
 * @vitest-environment jsdom
 */
import React from "react";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import DashboardNewsFeed from "../components/DashboardNewsFeed";

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

const RESEARCH_POST_GS = {
  id: "research-gs-1",
  title: "Goldman Sachs Equity Flows",
  content: "Institutional desks reported strong systematic buying.",
  timestamp: new Date().toISOString(),
  images: ["/api/newsfeed/research/files/1111111111111111111111111111111111111111111111111111111111111111.png"],
  rawImages: [],
  tags: ["FLOWS", "EQUITIES"],
  source: {
    kind: "dropbox",
    publisher: "Goldman Sachs",
    url: "/api/newsfeed/research/files/1111111111111111111111111111111111111111111111111111111111111111.pdf",
    documentDate: "2026-09-20",
    folderDate: "2026-09-20",
    pages: [1],
    figures: [
      {
        url: "/api/newsfeed/research/files/1111111111111111111111111111111111111111111111111111111111111111.png",
        page: 1,
        caption: "GS Cumulative Institutional Flow",
      },
    ],
    fileId: "id:gs1",
    revision: "rev1",
    contentHash: "hash1",
  },
};

const RESEARCH_POST_BOFA = {
  id: "research-bofa-1",
  title: "BofA Credit Strategy",
  content: "High yield spreads remain compressed despite issuance.",
  timestamp: new Date().toISOString(),
  images: [],
  rawImages: [],
  tags: ["CREDIT"],
  source: {
    kind: "dropbox",
    publisher: "BofA Global Research",
    url: "/api/newsfeed/research/files/2222222222222222222222222222222222222222222222222222222222222222.pdf",
    documentDate: "2026-09-20",
    folderDate: "2026-09-20",
    pages: [2],
    figures: [],
    fileId: "id:bofa1",
    revision: "rev1",
    contentHash: "hash2",
  },
};

const RESEARCH_POST_MIZUHO = {
  id: "research-mizuho-1",
  title: "Mizuho Macro Note",
  content: "BOJ rate path implications for global carry trades.",
  timestamp: new Date().toISOString(),
  images: [],
  rawImages: [],
  tags: ["MACRO"],
  source: {
    kind: "dropbox",
    publisher: "Mizuho",
    url: "/api/newsfeed/research/files/3333333333333333333333333333333333333333333333333333333333333333.pdf",
    documentDate: "2026-09-20",
    folderDate: "2026-09-20",
    pages: [3],
    figures: [],
    fileId: "id:mizuho1",
    revision: "rev1",
    contentHash: "hash3",
  },
};

const RESEARCH_POST_FALLBACK = {
  id: "research-unknown-1",
  title: "Independent Boutique Analysis",
  content: "Commodity balance updates.",
  timestamp: new Date().toISOString(),
  images: [],
  rawImages: [],
  tags: ["COMMODITIES"],
  source: {
    kind: "dropbox",
    publisher: "TS Lombard",
    url: "/api/newsfeed/research/files/4444444444444444444444444444444444444444444444444444444444444444.pdf",
    documentDate: "2026-09-20",
    folderDate: "2026-09-20",
    pages: [1],
    figures: [],
    fileId: "id:ts1",
    revision: "rev1",
    contentHash: "hash4",
  },
};

const STANDARD_POST = {
  id: "marketear-1",
  title: "Standard Market Ear Post",
  content: "Overview of morning options volume.",
  timestamp: new Date().toISOString(),
  images: ["/media/standard.png"],
  rawImages: [],
  tags: ["OPTIONS"],
};

beforeEach(() => {
  fetchMock.mockReset();
  // @ts-expect-error overriding fetch for test
  global.fetch = fetchMock;
});

afterEach(() => {
  cleanup();
});

describe("DashboardNewsFeed - PDF research publisher icons and fallback", () => {
  it("renders Goldman Sachs logo in top badge and footer link for GS research document", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [RESEARCH_POST_GS],
    });

    render(<DashboardNewsFeed />);

    await waitFor(() => {
      expect(screen.getByText("Goldman Sachs Equity Flows")).toBeDefined();
    });

    const badges = screen.getAllByTestId("news-feed-publisher-badge");
    expect(badges.length).toBe(1);
    expect(badges[0].textContent).toContain("Goldman Sachs");
    expect(badges[0].textContent).toContain("Research");

    const logos = screen.getAllByTestId("publisher-logo");
    // Badge logo + footer link logo
    expect(logos.length).toBe(2);
    expect(logos[0].getAttribute("data-publisher-id")).toBe("goldman-sachs");
    expect(logos[0].getAttribute("data-is-fallback")).toBe("false");
    expect(logos[1].getAttribute("data-publisher-id")).toBe("goldman-sachs");
  });

  it("renders Bank of America logo for BofA research", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [RESEARCH_POST_BOFA],
    });

    render(<DashboardNewsFeed />);

    await waitFor(() => {
      expect(screen.getByText("BofA Credit Strategy")).toBeDefined();
    });

    const badges = screen.getAllByTestId("news-feed-publisher-badge");
    expect(badges.length).toBe(1);
    expect(badges[0].textContent).toContain("BofA Global Research");

    const logos = screen.getAllByTestId("publisher-logo");
    expect(logos[0].getAttribute("data-publisher-id")).toBe("bank-of-america");
    expect(logos[0].getAttribute("data-is-fallback")).toBe("false");
  });

  it("renders Mizuho logo for Mizuho research", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [RESEARCH_POST_MIZUHO],
    });

    render(<DashboardNewsFeed />);

    await waitFor(() => {
      expect(screen.getByText("Mizuho Macro Note")).toBeDefined();
    });

    const badges = screen.getAllByTestId("news-feed-publisher-badge");
    expect(badges.length).toBe(1);
    expect(badges[0].textContent).toContain("Mizuho");

    const logos = screen.getAllByTestId("publisher-logo");
    expect(logos[0].getAttribute("data-publisher-id")).toBe("mizuho");
    expect(logos[0].getAttribute("data-is-fallback")).toBe("false");
  });

  it("renders fallback default icon for unmapped publisher", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [RESEARCH_POST_FALLBACK],
    });

    render(<DashboardNewsFeed />);

    await waitFor(() => {
      expect(screen.getByText("Independent Boutique Analysis")).toBeDefined();
    });

    const badges = screen.getAllByTestId("news-feed-publisher-badge");
    expect(badges.length).toBe(1);
    expect(badges[0].textContent).toContain("TS Lombard");

    const logos = screen.getAllByTestId("publisher-logo");
    expect(logos[0].getAttribute("data-publisher-id")).toBe("default");
    expect(logos[0].getAttribute("data-is-fallback")).toBe("true");
  });

  it("does not render publisher badge for standard non-PDF articles", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [STANDARD_POST],
    });

    render(<DashboardNewsFeed />);

    await waitFor(() => {
      expect(screen.getByText("Standard Market Ear Post")).toBeDefined();
    });

    expect(screen.queryByTestId("news-feed-publisher-badge")).toBeNull();
    expect(screen.queryByTestId("publisher-logo")).toBeNull();
  });

  it("renders publisher logo and badge inside lightbox modal when opened", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [RESEARCH_POST_GS],
    });

    render(<DashboardNewsFeed />);

    await waitFor(() => {
      expect(screen.getByText("Goldman Sachs Equity Flows")).toBeDefined();
    });

    // Open lightbox
    const imageBtn = screen.getByRole("button", { name: /Open lightbox for/i });
    fireEvent.click(imageBtn);

    await waitFor(() => {
      expect(screen.getByTestId("lightbox-publisher-badge")).toBeDefined();
    });

    const lightboxBadge = screen.getByTestId("lightbox-publisher-badge");
    expect(lightboxBadge.textContent).toContain("Goldman Sachs");
  });
});
