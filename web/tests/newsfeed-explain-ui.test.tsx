/** @vitest-environment jsdom */
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import NewsfeedExplain from "@/components/NewsfeedExplain";
import DashboardNewsFeed from "@/components/DashboardNewsFeed";
import { encodeExplainEvent } from "@/lib/newsfeedExplainProgress";

vi.mock("next/image", () => ({
  default: ({ src, alt, className }: { src: string; alt: string; className?: string }) =>
    React.createElement("img", { src, alt, className }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("@/lib/useBookmarks", () => ({
  useBookmarks: () => ({ isBookmarked: () => false, toggleBookmark: vi.fn() }),
}));
vi.mock("@/lib/useHeadlines", () => ({
  useHeadlines: () => ({ items: [], status: "live" }),
}));

const post = {
  id: "pjm-2028-capacity",
  title: "PJM 2028/2029 Capacity Auction Cleared at $325/MW-Day Cap",
  content: "The auction cleared at the cap of $325/MW-day and came up 6.8 GW short of its reliability requirement.",
  timestamp: "2026-10-17T18:37:00Z",
  isoTimestamp: "2026-10-17T18:37:00Z",
  href: "https://example.com/pjm",
  images: [] as string[],
  tags: ["POWER-MARKETS", "ENERGY"],
};

const chartPost = {
  ...post,
  id: "pjm-chart",
  images: ["https://media.radon.run/images/pjm-chart.png"],
};

function streamResponse(explanation: string, cached = false) {
  const body = [
    encodeExplainEvent("stage", { stage: "queued", budgetMs: 90_000 }),
    encodeExplainEvent("stage", { stage: "drafting" }),
    encodeExplainEvent("result", {
      explanation,
      cached,
      thin: false,
      model: "claude-opus-5",
      provider: "anthropic",
      hasImage: false,
    }),
  ].join("");
  return {
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": "text/event-stream; charset=utf-8" }),
    body: new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body));
        controller.close();
      },
    }),
    text: async () => body,
    json: async () => ({ explanation }),
  } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async (url: RequestInfo) => {
    const href = typeof url === "string" ? url : url.url;
    if (href.includes("/api/newsfeed/posts")) {
      return { ok: true, status: 200, headers: new Headers(), json: async () => [post, chartPost] };
    }
    if (href === "/api/newsfeed/explain") {
      return streamResponse("The auction paid plants to stay available. A capacity auction buys standby power.");
    }
    return { ok: true, status: 200, headers: new Headers(), json: async () => ([]) };
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("NewsfeedExplain", () => {
  it("sits beside Share on the feed card and stays idle until armed", async () => {
    render(<DashboardNewsFeed />);
    const item = (await screen.findAllByText(/PJM 2028/))[0];
    const card = item.closest("[data-testid='news-feed-item']") ?? document.body;
    expect(card.querySelector("[data-testid='newsfeed-explain']")).toBeTruthy();
    expect(card.querySelector("[data-newsfeed-share]")).toBeTruthy();
    const explain = screen.getAllByRole("button", { name: "Explain" })[0];
    expect(explain.getAttribute("aria-expanded")).toBe("false");
    expect(fetchMock.mock.calls.some(([url, init]) =>
      (typeof url === "string" ? url : url.url) === "/api/newsfeed/explain"
      && (init as RequestInit | undefined)?.method === "POST",
    )).toBe(false);
  });

  it("posts the full explain path and payload when armed, then shows collapse", async () => {
    render(
      <NewsfeedExplain
        post={post}
        imageUrls={[]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Explain" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const call = fetchMock.mock.calls.find(([url]) =>
      (typeof url === "string" ? url : url.url) === "/api/newsfeed/explain",
    );
    expect(call).toBeTruthy();
    const init = call![1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.cache).toBe("no-store");
    expect(JSON.parse(String(init.body))).toEqual({
      postId: post.id,
      title: post.title,
      content: post.content,
      imageUrls: [],
    });
    expect(await screen.findByText(/capacity auction/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Hide explanation" }));
    expect(screen.queryByText(/capacity auction/i)).toBeNull();
  });

  it("sends chart urls with the post and keeps loading copy free of slop", async () => {
    render(<NewsfeedExplain post={chartPost} imageUrls={chartPost.images} />);
    fireEvent.click(screen.getByRole("button", { name: "Explain" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const call = fetchMock.mock.calls.find(([url]) =>
      (typeof url === "string" ? url : url.url) === "/api/newsfeed/explain",
    );
    expect(JSON.parse(String((call![1] as RequestInit).body)).imageUrls).toEqual(chartPost.images);
    expect(document.body.textContent).not.toMatch(/Oops|Unleash|at a glance/i);
  });

  it("retries from ErrorToast and never fires while the request is in flight", async () => {
    let resolveExplain!: (value: Response) => void;
    fetchMock.mockImplementation((url: RequestInfo) => {
      const href = typeof url === "string" ? url : url.url;
      if (href === "/api/newsfeed/explain") {
        return new Promise<Response>((resolve) => { resolveExplain = resolve; });
      }
      return Promise.resolve({ ok: true, json: async () => [] } as Response);
    });
    render(<NewsfeedExplain post={post} imageUrls={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Explain" }));
    expect((screen.getByRole("button", { name: /Explain/ }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => {
      resolveExplain({
        ok: false,
        status: 502,
        headers: new Headers({ "content-type": "application/json" }),
        json: async () => ({ error: "fail" }),
        text: async () => JSON.stringify({ error: "fail" }),
      } as Response);
    });
    await screen.findByText(/Could not explain this post/);
    const before = fetchMock.mock.calls.filter(([url]) =>
      (typeof url === "string" ? url : url.url) === "/api/newsfeed/explain",
    ).length;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) =>
      (typeof url === "string" ? url : url.url) === "/api/newsfeed/explain",
    ).length).toBe(before + 1));
  });
});
