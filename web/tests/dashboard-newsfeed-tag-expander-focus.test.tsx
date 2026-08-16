/**
 * @vitest-environment jsdom
 */

/**
 * Defect F — the `+N` tag expander drops keyboard focus to <body>.
 *
 * `.tagsExpanded .tagMore { display: none }` hides the button while it still
 * holds focus, so the browser resets the active element to <body> and the
 * keyboard user loses their place mid-list. `aria-expanded` on a control that
 * ceases to exist is inert too: nothing announces the disclosure, and the
 * attribute keeps claiming a state for an element the user can no longer reach.
 *
 * Contract pinned here (the "keep it coherent" half of the resolution):
 *   - Activating `+N` UNMOUNTS the expander. There is no hidden-but-present
 *     control left holding a stale `aria-expanded="true"`.
 *   - Focus is deliberately moved to the first newly revealed overflow chip,
 *     which is the nearest meaningful target and the natural reading position.
 *   - Focus therefore never lands on <body>.
 *
 * jsdom applies no CSS, so the `display: none` focus drop itself is not
 * observable here — the browser half is pinned in
 * web/e2e/mobile-newsfeed-layout.spec.ts. What IS observable, and what is red
 * today, is that the component takes no deliberate focus action at all.
 */

import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

const TAGS = ["SPX", "VOLATILITY", "OPTIONS", "MACRO", "DEALER-GAMMA", "SKEW"];

const POST = {
  id: "focus-1",
  title: "Single stock vol",
  content: "Dealers are short gamma into the close.",
  timestamp: new Date(Date.now() - 26 * 60_000).toISOString(),
  images: [] as string[],
  tags: TAGS,
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

async function renderFeed() {
  render(<DashboardNewsFeed />);
  await waitFor(() => {
    expect(screen.queryAllByRole("listitem").length).toBeGreaterThan(0);
  });
  return document.querySelector("div.news-feed-tags") as HTMLElement;
}

function tagChips(container: HTMLElement): HTMLButtonElement[] {
  return Array.from(container.querySelectorAll<HTMLButtonElement>("button.news-feed-tag-chip"));
}

describe("+N tag expander — focus and ARIA", () => {
  it("moves focus to the first revealed overflow chip instead of dropping it", async () => {
    const container = await renderFeed();
    const more = within(container).getByRole("button", { name: /more tags/i });
    more.focus();
    expect(document.activeElement).toBe(more);

    fireEvent.click(more);

    const expanded = document.querySelector("div.news-feed-tags") as HTMLElement;
    // Chips 0-3 are always visible; index 4 is the first one the expander reveals.
    const firstRevealed = tagChips(expanded)[4];
    expect(firstRevealed).toBeDefined();
    expect(firstRevealed.textContent).toBe(TAGS[4]);

    await waitFor(() => {
      expect(document.activeElement).toBe(firstRevealed);
    });
  });

  it("never leaves focus on <body> after the expander activates", async () => {
    const container = await renderFeed();
    const more = within(container).getByRole("button", { name: /more tags/i });
    more.focus();

    fireEvent.click(more);

    await waitFor(() => {
      expect(document.activeElement).not.toBe(document.body);
      expect(document.activeElement).not.toBeNull();
    });
  });

  it("removes the expander from the tree rather than leaving an inert aria-expanded", async () => {
    const container = await renderFeed();
    const more = within(container).getByRole("button", { name: /more tags/i });
    // Before activation the control is a coherent collapsed disclosure.
    expect(more.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(more);

    const expanded = document.querySelector("div.news-feed-tags") as HTMLElement;
    expect(
      within(expanded).queryByRole("button", { name: /more tags/i }),
      "a hidden button still claiming aria-expanded=true is inert, not accessible",
    ).toBeNull();
    expect(expanded.querySelectorAll("[aria-expanded]")).toHaveLength(0);
    // Every tag is now a real, reachable chip.
    expect(tagChips(expanded).map((c) => c.textContent)).toEqual(TAGS);
  });
});
