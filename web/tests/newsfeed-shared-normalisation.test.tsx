/**
 * @vitest-environment jsdom
 */

/**
 * Defect D — the rail and the lightbox disagreed about the same post's body,
 * one tap apart. `normalisePostContent` lived in DashboardNewsFeed.tsx and was
 * applied only where the rail rendered the summary; NewsfeedLightbox rendered
 * `post.content` raw. Measured on the same post: rail startsQuote false,
 * lightbox startsQuote true.
 *
 * The fix is a single source of truth: the normaliser moves to
 * `web/lib/newsfeedText.ts` and is applied at the ONE shared boundary every
 * consumer already funnels through — `useNewsfeedPosts` — so the rail, the
 * lightbox, the bookmarks snapshot and the profile list all read the same
 * bytes. Applying it at the hook means desktop gets it too, which is correct:
 * an unpartnered scrape artifact is bad data everywhere, not just on mobile.
 *
 * These tests drive the invariant through the HOOK boundary, not through the
 * component, so a future consumer that renders `post.content` directly is
 * covered by construction.
 */

import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

/** The live artifact shape: a leading U+0022 with no partner in the body. */
const RAW_ARTIFACT =
  '"Today, we estimate that only ~45% of the S&P 500 by weight is eligible to repurchase shares.';
const EXPECTED =
  "Today, we estimate that only ~45% of the S&P 500 by weight is eligible to repurchase shares.";

const POST = {
  id: "shared-norm-1",
  title: "Buyback eligibility",
  content: RAW_ARTIFACT,
  timestamp: new Date(Date.now() - 12 * 60_000).toISOString(),
  images: ["/media/shared-norm-1.png"],
  rawImages: ["https://themarketear.com/images/shared-norm-1.png"],
  tags: ["SPX", "BUYBACKS"],
};

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers(),
    json: async () => [POST],
  } as unknown as Response);
  // @ts-expect-error — overriding global fetch for test
  global.fetch = fetchMock;
  if (!("scrollIntoView" in Element.prototype)) {
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
  } else {
    vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => {});
  }
});

afterEach(() => {
  cleanup();
});

function HookProbe() {
  const { posts } = useNewsfeedPosts();
  if (posts.length === 0) return null;
  return <div data-testid="probe-content">{posts[0].content}</div>;
}

describe("newsfeed text normalisation is applied once, at the shared hook boundary", () => {
  it("useNewsfeedPosts hands every consumer the normalised body", async () => {
    render(<HookProbe />);
    const probe = await screen.findByTestId("probe-content");
    // Deliberately compared against a literal, not against a call into the
    // normaliser: the point is that the HOOK, not the caller, owns the pass.
    expect(probe.textContent).toBe(EXPECTED);
    expect(probe.textContent!.startsWith('"')).toBe(false);
  });

  it("the rail summary and the lightbox body are byte-identical for the same post", async () => {
    render(<DashboardNewsFeed />);

    await waitFor(() => {
      expect(document.querySelector("p.news-feed-summary")).not.toBeNull();
    });
    const railText = document.querySelector("p.news-feed-summary")!.textContent;

    fireEvent.click(screen.getByRole("button", { name: `Open lightbox for: ${POST.title}` }));

    await waitFor(() => {
      expect(document.querySelector(".newsfeed-lightbox__body")).not.toBeNull();
    });
    const lightboxText = document.querySelector(".newsfeed-lightbox__body")!.textContent;

    expect(lightboxText).toBe(railText);
    expect(lightboxText).toBe(EXPECTED);
    expect(lightboxText!.startsWith('"')).toBe(false);
  });

  it("the component no longer owns a private copy of the normaliser", async () => {
    const mod = await import("../components/DashboardNewsFeed");
    expect(
      Object.keys(mod),
      "normalisePostContent must live in lib/newsfeedText.ts, not in the component",
    ).not.toContain("normalisePostContent");
  });
});
