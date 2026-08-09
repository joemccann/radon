/**
 * @vitest-environment jsdom
 *
 * FeedPanel — featured story + headline list. Pinned behaviours:
 *  - newest post renders as the featured story
 *  - clicking a headline swaps the featured story
 *  - a featured story without an image omits the media slot
 *  - FULL FEED links to the external feed origin
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";

import FeedPanel from "@/components/dashboard/FeedPanel";

vi.mock("next/image", () => ({
  default: (props: Record<string, unknown>) => {
    const { src, alt } = props as { src: string; alt: string };
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={src} alt={alt} data-testid="feed-image" />;
  },
}));

function post(id: string, title: string, ts: string, images: string[] = []) {
  return { id, title, content: `${title} body`, timestamp: ts, images, tags: ["SEMIS"] };
}

const POSTS = [
  post("a", "The memory bull", "2026-08-07T11:00:00Z", ["/media/a.png"]),
  post("b", "Vol term structure steepens", "2026-08-07T10:00:00Z"),
  post("c", "Breadth divergence persists", "2026-08-07T09:00:00Z", ["/media/c.png"]),
];

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(POSTS), { status: 200 })),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FeedPanel", () => {
  it("features the newest post with its image", async () => {
    render(<FeedPanel />);
    await waitFor(() => expect(screen.getByTestId("feed-featured")).toBeTruthy());
    expect(screen.getByTestId("feed-featured").textContent).toContain("The memory bull");
    expect(screen.getByTestId("feed-image")).toBeTruthy();
  });

  it("swaps the featured story when a headline is clicked", async () => {
    render(<FeedPanel />);
    await waitFor(() => expect(screen.getByTestId("feed-featured")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /Vol term structure steepens/ }));
    expect(screen.getByTestId("feed-featured").textContent).toContain("Vol term structure steepens");
    // imageless story omits the media slot entirely
    expect(screen.queryByTestId("feed-image")).toBeNull();
  });

  it("marks the active headline", async () => {
    render(<FeedPanel />);
    await waitFor(() => expect(screen.getByTestId("feed-featured")).toBeTruthy());
    const active = document.querySelector(".feed-panel__headline.is-active");
    expect(active?.textContent).toContain("The memory bull");
  });

  it("links FULL FEED to the external origin", async () => {
    render(<FeedPanel />);
    await waitFor(() => expect(screen.getByTestId("feed-featured")).toBeTruthy());
    const link = screen.getByRole("link", { name: /full feed/i }) as HTMLAnchorElement;
    expect(link.href).toContain("themarketear.com");
  });

  it("renders the empty state when no posts arrive", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify([]), { status: 200 })),
    );
    render(<FeedPanel />);
    await waitFor(() => expect(screen.getByText(/No Market Ear posts/)).toBeTruthy());
  });
});
