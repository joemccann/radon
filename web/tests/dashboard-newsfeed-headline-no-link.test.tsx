/**
 * @vitest-environment jsdom
 *
 * Feed headlines must NOT be outbound links to the source (Market Ear).
 * The footer Link pill remains the only outbound anchor per post.
 */
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

const POST = {
  id: "p1",
  title: "Positioning",
  content: "Hedge fund and mutual fund equity market exposures.",
  timestamp: new Date(Date.now() - 26 * 60_000).toISOString(),
  images: ["/media/p1-01.png"],
  rawImages: ["https://themarketear.com/images/p1.png"],
  tags: ["EQUITIES", "POSITIONING"],
};

beforeEach(() => {
  fetchMock.mockReset();
  // @ts-expect-error — overriding global fetch for test
  global.fetch = fetchMock;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("DashboardNewsFeed headline", () => {
  async function renderFeed() {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [POST],
    } as Response);
    render(React.createElement(DashboardNewsFeed));
    await waitFor(() => {
      expect(screen.queryAllByRole("listitem").length).toBeGreaterThan(0);
    });
  }

  it("does not wrap the headline in an outbound anchor", async () => {
    await renderFeed();
    const headline = screen.getByRole("heading", { name: "Positioning" });
    expect(headline.closest("a")).toBeNull();
  });

  it("keeps the footer Link pill as the only outbound anchor", async () => {
    await renderFeed();
    const item = screen.getAllByTestId("news-feed-item")[0];
    const anchors = item.querySelectorAll("a[href]");
    expect(anchors).toHaveLength(1);
    expect(anchors[0].getAttribute("data-testid")).toBe("news-feed-link-pill");
  });
});
