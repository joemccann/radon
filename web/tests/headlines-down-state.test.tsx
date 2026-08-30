/**
 * @vitest-environment jsdom
 *
 * REL-155 (R-463): a populated headlines tape must not render identically to
 * live once the hub or its upstream is down. The tape gates only on
 * `items.length === 0`, so a frozen tape read as live; the hook ignored the
 * hub's `upstream-open` frame so the badge stayed off after recovery; and the
 * footer under the Headlines tab reported the COMMENTARY feed's freshness.
 */
import React from "react";
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import HeadlinesTape from "../components/dashboard/HeadlinesTape";
import type { Headline } from "../lib/useHeadlines";

vi.mock("next/image", () => ({
  default: ({ src, alt }: { src: string; alt: string }) => React.createElement("img", { src, alt }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(""),
}));

vi.mock("../lib/useNewsfeedPosts", () => ({
  useNewsfeedPosts: () => ({
    posts: [
      {
        id: "p1",
        title: "What to watch",
        content: "Commentary body",
        href: "https://example.test/p1",
        isoTimestamp: "2026-08-29T16:00:00.000Z",
        timestamp: "2026-08-29T16:00:00.000Z",
        images: [],
        tags: ["MACRO"],
      },
    ],
    loading: false,
    refreshing: false,
    error: null,
    lastUpdated: "2026-08-30T12:00:00.000Z",
    refresh: vi.fn(),
  }),
}));

const headlinesState = vi.hoisted(() => ({
  items: [] as unknown[],
  status: "live" as string,
}));

vi.mock("../lib/useHeadlines", async () => {
  const actual = await vi.importActual<typeof import("../lib/useHeadlines")>("../lib/useHeadlines");
  return { ...actual, useHeadlines: () => ({ items: headlinesState.items, status: headlinesState.status }) };
});

vi.mock("../lib/headlinesSocket", () => ({
  buildHeadlinesWebSocketUrl: async () => "ws://localhost:8766/ws-headlines",
  headlinesUrlLeaksUpstream: () => false,
}));

const NOW = Date.parse("2026-08-30T14:00:00.000Z");

function headline(id: string, time: string | null, content = `print ${id}`): Headline {
  return { kind: "headline", id, time, important: false, content, impact: [] };
}

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  url: string;
  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  frame(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
  headlinesState.items = [];
  headlinesState.status = "live";
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("HeadlinesTape down state", () => {
  const items = [
    headline("old", "2026-08-30T13:00:00.000Z"),
    headline("new", "2026-08-30T13:48:00.000Z", "Newest print."),
  ];

  it("renders a down banner with the age of the newest print, and no banner while live", () => {
    const { container: liveContainer } = render(<HeadlinesTape items={items} status="live" />);
    expect(liveContainer.querySelector(".headlines-tape__down")).toBeNull();
    cleanup();

    render(<HeadlinesTape items={items} status="down" />);
    const banner = screen.getByTestId("headlines-tape-down");
    expect(banner.textContent).toContain("Headlines feed down");
    expect(banner.textContent).toContain("12m ago");
    // The prints stay on screen underneath the banner.
    expect(screen.getAllByTestId("headlines-tape-row")).toHaveLength(2);
  });

  it("keeps ticking the age while down", () => {
    render(<HeadlinesTape items={items} status="down" />);
    act(() => {
      vi.advanceTimersByTime(3 * 60_000);
    });
    expect(screen.getByTestId("headlines-tape-down").textContent).toContain("15m ago");
  });

  it("says so when no print carries a time", () => {
    render(<HeadlinesTape items={[headline("x", null)]} status="down" />);
    expect(screen.getByTestId("headlines-tape-down").textContent).toContain("Last print time unknown");
  });
});

describe("useHeadlines upstream-open", () => {
  it("flips the badge back to live when the hub reports upstream-open after a down", async () => {
    const { useHeadlines } = await vi.importActual<typeof import("../lib/useHeadlines")>("../lib/useHeadlines");
    const { result } = renderHook(() => useHeadlines());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    const socket = MockWebSocket.instances[0];
    expect(socket).toBeDefined();
    act(() => socket.frame({ type: "snapshot", items: [headline("a", null)] }));
    expect(result.current.status).toBe("live");
    act(() => socket.frame({ type: "status", state: "upstream-down" }));
    expect(result.current.status).toBe("down");
    act(() => socket.frame({ type: "status", state: "upstream-open" }));
    expect(result.current.status).toBe("live");
  });
});

describe("DashboardNewsFeed footer scoping", () => {
  it("reports the headlines feed's own basis and newest print under the Headlines tab", async () => {
    const { default: DashboardNewsFeed } = await import("../components/DashboardNewsFeed");
    headlinesState.items = [headline("h1", "2026-08-30T13:48:00.000Z", "Newest print.")];
    headlinesState.status = "down";
    render(<DashboardNewsFeed />);
    const rail = () => screen.getByLabelText("Feed calibration").textContent ?? "";
    // Commentary tab: the scraper's own fields, untouched.
    expect(rail()).toContain("scraper");
    fireEvent.click(screen.getByRole("tab", { name: "Headlines" }));
    expect(rail()).toContain("fault");
    expect(rail()).not.toContain("scraper");
    const newest = new Date("2026-08-30T13:48:00.000Z").toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    expect(rail()).toContain(newest);
    expect(screen.queryByText("LIVE")).toBeNull();
    expect(screen.getByTestId("headlines-tape-down")).toBeTruthy();
  });
});
