/**
 * @vitest-environment jsdom
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DashboardNewsFeed from "../components/DashboardNewsFeed";
import styles from "../components/DashboardNewsFeed.module.css";
import { normalisePostContent } from "../lib/newsfeedText";
import { formatCompact, formatRelative, formatTime } from "../lib/newsfeedTime";

vi.mock("next/image", () => ({
  default: ({ src, alt, className }: { src: string; alt: string; className?: string }) =>
    React.createElement("img", { src, alt, className }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(""),
}));

const moduleCss = readFileSync(
  join(__dirname, "..", "components", "DashboardNewsFeed.module.css"),
  "utf8",
);

function ruleBlock(source: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = source.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "m"));
  expect(match, `rule not found for ${selector}`).not.toBeNull();
  return match![1];
}

const TAGS = ["SPX", "VOLATILITY", "OPTIONS", "MACRO", "DEALER-GAMMA", "SKEW"];
const RAW_SUMMARY = '"Short-dated SPX realized vol remains pinned near the floor';

const fetchMock = vi.fn();

function makePost(overrides: Record<string, unknown> = {}) {
  return {
    id: "p1",
    title: "Single stock vol",
    content: RAW_SUMMARY,
    // Window-relative so the compact token never rots in CI.
    timestamp: new Date(Date.now() - 26 * 60_000).toISOString(),
    images: ["/media/p1-01.png"],
    rawImages: ["https://themarketear.com/images/p1.png"],
    tags: TAGS,
    ...overrides,
  };
}

async function renderFeed(posts: unknown[]) {
  fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => posts } as Response);
  const utils = render(React.createElement(DashboardNewsFeed));
  await waitFor(() => {
    expect(screen.queryAllByRole("listitem").length).toBeGreaterThan(0);
  });
  return utils;
}

beforeEach(() => {
  fetchMock.mockReset();
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

describe("normalisePostContent", () => {
  it("drops an opening quote with no closing partner", () => {
    expect(normalisePostContent('"Buyback bid is coming back')).toBe(
      "Buyback bid is coming back",
    );
    expect(normalisePostContent("“The SEC data shows")).toBe("The SEC data shows");
  });

  it("keeps a balanced pull quote intact", () => {
    expect(normalisePostContent('"Fully quoted line."')).toBe('"Fully quoted line."');
  });

  it("never touches an intra-word apostrophe", () => {
    expect(normalisePostContent("The SEC's data shows")).toBe("The SEC's data shows");
    expect(normalisePostContent("doesn’t matter")).toBe("doesn’t matter");
  });

  // Defect C: parity counting inverted on word-final apostrophes, so the rule
  // is now "strip a leading double quote only when no other double quote
  // appears anywhere later". A later double quote proves the opener is
  // partnered somewhere; guessing which pair is which is the inversion itself.
  it("keeps the opener when another double quote appears later", () => {
    expect(normalisePostContent('"He said "yes" and left')).toBe('"He said "yes" and left');
  });

  it("collapses runaway blank lines but keeps a paragraph break", () => {
    expect(normalisePostContent("a\n\n\n\nb")).toBe("a\n\nb");
    expect(normalisePostContent("a\r\n\r\nb")).toBe("a\n\nb");
  });
});

describe("DashboardNewsFeed mobile item", () => {
  it("renders the summary without the unpartnered leading quote", async () => {
    await renderFeed([makePost()]);
    const summary = document.querySelector("p.news-feed-summary");
    expect(summary).not.toBeNull();
    expect(summary!.textContent!.startsWith('"')).toBe(false);
    expect(summary!.textContent).toBe(RAW_SUMMARY.slice(1));
  });

  it("caps the visible chips at four and offers a tappable +N expander", async () => {
    await renderFeed([makePost()]);

    const container = document.querySelector("div.news-feed-tags")!;
    expect(container.className).toContain(styles.tags);
    expect(container.className).not.toContain(styles.tagsExpanded);

    const chips = Array.from(container.querySelectorAll("button.news-feed-tag-chip"));
    // 6 tags + the +N chip
    expect(chips).toHaveLength(7);
    chips.slice(0, 4).forEach((chip) => {
      expect(chip.className).toContain(styles.tagChip);
      expect(chip.className).not.toContain(styles.tagOverflow);
    });
    chips.slice(4, 6).forEach((chip) => {
      expect(chip.className).toContain(styles.tagOverflow);
    });

    const more = within(container as HTMLElement).getByRole("button", {
      name: "Show 2 more tags",
    });
    expect(more.textContent).toBe("+2");
    expect(more.className).toContain(styles.tagMore);
    expect(more.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(more);

    const expanded = document.querySelector("div.news-feed-tags")!;
    expect(expanded.className).toContain(styles.tagsExpanded);
    // Defect F: the expander unmounts on activation. A button hidden by
    // `display: none` while still claiming aria-expanded="true" is inert, and
    // hiding a focused control drops focus out of the list.
    expect(
      within(expanded as HTMLElement).queryByRole("button", { name: "Show 2 more tags" }),
    ).toBeNull();
    expect(expanded.querySelectorAll("[aria-expanded]")).toHaveLength(0);
    expect(expanded.querySelectorAll("button.news-feed-tag-chip")).toHaveLength(TAGS.length);
  });

  it("renders no +N chip when the post has four tags or fewer", async () => {
    await renderFeed([makePost({ tags: TAGS.slice(0, 4) })]);
    const container = document.querySelector("div.news-feed-tags")!;
    expect(container.querySelectorAll("button.news-feed-tag-chip")).toHaveLength(4);
    expect(
      within(container as HTMLElement).queryByRole("button", { name: /more tags/i }),
    ).toBeNull();
  });

  it("prints a single-token compact stamp beside the full desktop stamp", async () => {
    const post = makePost();
    await renderFeed([post]);

    const stamp = document.querySelector("span.news-feed-timestamp")!;
    expect(stamp.className).toContain(styles.timestamp);

    const compact = stamp.querySelector(`.${styles.tsCompact}`)!;
    const full = stamp.querySelector(`.${styles.tsFull}`)!;
    expect(compact.textContent).toBe(formatCompact(post.timestamp));
    expect(compact.textContent).toMatch(/^\d+ min ago$/);
    expect(full.textContent).toBe(
      `${formatRelative(post.timestamp)} at ${formatTime(post.timestamp)}`,
    );
  });

  it("applies the module classes alongside the existing global class names", async () => {
    await renderFeed([makePost()]);

    const expectations: Array<[string, string]> = [
      ["section.dashboard-news", styles.card],
      ["header.dashboard-news__header", styles.header],
      ["div.dashboard-news__heading", styles.heading],
      ["div.news-feed-actions", styles.actions],
      ["button.news-feed-refresh--rail", styles.refresh],
      ["ul.news-feed-list", styles.list],
      ["li.news-feed-item", styles.item],
      ["h3.news-feed-headline", styles.headline],
      ["p.news-feed-summary", styles.summary],
      ["button.news-feed-image-wrapper--button", styles.imageWrapper],
      ["img.news-feed-image", styles.image],
      ["div.news-feed-footer", styles.footer],
      ["a.news-feed-link-pill", styles.linkPill],
    ];

    for (const [selector, moduleClass] of expectations) {
      const node = document.querySelector(selector);
      expect(node, `missing ${selector}`).not.toBeNull();
      expect(node!.className, selector).toContain(moduleClass);
    }
  });
});

describe("DashboardNewsFeed.module.css mobile rules", () => {
  const mobile = 'body[data-mobile="true"]';

  it("scopes every layout rule to the mobile shell so desktop cannot regress", () => {
    const selectors = moduleCss
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split("}")
      .map((chunk) => chunk.split("{")[0].trim())
      .filter((sel) => sel.length > 0 && !sel.startsWith("/*") && !sel.startsWith("@"));
    const unscoped = selectors.filter((sel) => !sel.includes(mobile));
    // Only the two desktop `display: none` defaults may be unscoped. `.tagMore`
    // carries the doubled-class bump like every other rule in the file: a bare
    // `.tagMore` is (0,1,0), a tie with globals' `.news-feed-tag-chip
    // { display: inline-flex }` that resolves on injection order alone.
    expect(unscoped).toEqual([".tsCompact", ".tagMore.tagMore"]);
  });

  it("drops the item's inline padding and moves to the four-step spacing scale", () => {
    const card = ruleBlock(moduleCss, `:global(${mobile}) .card.card`);
    expect(card).toMatch(/--nf-s1:\s*4px/);
    expect(card).toMatch(/--nf-s2:\s*8px/);
    expect(card).toMatch(/--nf-s3:\s*12px/);
    expect(card).toMatch(/--nf-s4:\s*16px/);
    expect(card).toMatch(/padding:\s*var\(--nf-s3\)\s+var\(--nf-s3\)\s+0/);

    const item = ruleBlock(moduleCss, `:global(${mobile}) .item.item`);
    expect(item).toMatch(/padding:\s*var\(--nf-s4\)\s+0/);
    expect(item).toMatch(/gap:\s*var\(--nf-s2\)/);
  });

  it("returns the header to a single row and hides the duplicated eyebrow", () => {
    const header = ruleBlock(moduleCss, `:global(${mobile}) .header.header`);
    expect(header).toMatch(/flex-direction:\s*row/);
    expect(header).toMatch(/flex-wrap:\s*nowrap/);
    expect(header).toMatch(/margin-bottom:\s*var\(--nf-s3\)/);

    const eyebrow = ruleBlock(
      moduleCss,
      `:global(${mobile}) .heading.heading :global(.panel-eyebrow)`,
    );
    expect(eyebrow).toMatch(/display:\s*none/);
  });

  it("tightens the type scale", () => {
    const headline = ruleBlock(moduleCss, `:global(${mobile}) .headline.headline`);
    expect(headline).toMatch(/font-size:\s*16px/);
    expect(headline).toMatch(/line-height:\s*1\.25/);

    const summary = ruleBlock(moduleCss, `:global(${mobile}) .summary.summary`);
    expect(summary).toMatch(/font-size:\s*var\(--text-body\)/);
    expect(summary).toMatch(/line-height:\s*1\.3846/);
    expect(summary).toMatch(/white-space:\s*pre-line/);
  });

  it("cancels the 44px button floor on chips and keeps a 24px box", () => {
    const chip = ruleBlock(moduleCss, `:global(${mobile}) .tagChip.tagChip`);
    expect(chip).toMatch(/min-height:\s*24px/);
    expect(chip).toMatch(/min-width:\s*auto/);
    expect(chip).toMatch(/position:\s*relative/);

    const hit = ruleBlock(moduleCss, `:global(${mobile}) .tagChip.tagChip::after`);
    // `inset` resolves against the containing block's PADDING box, and the chip's
    // 24px border box carries a 1px border, so the pad grows from 22px, not 24px.
    // -4px landed at 30px (the bug this pin used to encode); -5px lands at 32px.
    expect(hit).toMatch(/inset:\s*-5px\s+-2px/);
  });

  it("normalises the footer row to 24px with a non-wrapping timestamp", () => {
    const timestamp = ruleBlock(moduleCss, `:global(${mobile}) .timestamp.timestamp`);
    expect(timestamp).toMatch(/white-space:\s*nowrap/);
    expect(timestamp).toMatch(/min-height:\s*24px/);
    expect(timestamp).toMatch(/flex:\s*1\s+1\s+auto/);

    const pill = ruleBlock(moduleCss, `:global(${mobile}) .linkPill.linkPill`);
    expect(pill).toMatch(/min-height:\s*24px/);

    const star = ruleBlock(
      moduleCss,
      `:global(${mobile}) .footer.footer :global(.star-toggle)`,
    );
    expect(star).toMatch(/min-height:\s*24px/);
    expect(star).toMatch(/margin-left:\s*auto/);
  });

  it("keeps a 44px hit area on the pill, the star and the refresh rail", () => {
    expect(moduleCss).toMatch(/width:\s*var\(--touch-min\)/);
    expect(moduleCss).toMatch(/height:\s*var\(--touch-min\)/);
    const refresh = ruleBlock(moduleCss, `:global(${mobile}) .refresh.refresh`);
    expect(refresh).toMatch(/min-height:\s*24px/);
    expect(refresh).toMatch(/position:\s*relative/);
  });

  it("leaves one frame around the image", () => {
    const wrapper = ruleBlock(moduleCss, `:global(${mobile}) .imageWrapper.imageWrapper`);
    expect(wrapper).toMatch(/border:\s*0/);
    expect(wrapper).toMatch(/aspect-ratio:\s*16\s*\/\s*9/);
    const image = ruleBlock(moduleCss, `:global(${mobile}) .image.image`);
    expect(image).toMatch(/object-fit:\s*cover/);
  });

  it("clears the fixed tab bar without double-counting its reservation", () => {
    const card = ruleBlock(moduleCss, `:global(${mobile}) .card.card`);
    // The card must NOT carry a bottom inset. The feed is not the last block on
    // the mobile dashboard — `.dashboard-surface__right` follows it inside a grid
    // that already supplies the 16px seam, and `body[data-mobile="true"] .main`
    // already reserves the tab bar + safe area on the real scroll container. An
    // inset here is spent mid-scroll and blew the seam out to 62px on a notched
    // iPhone. `scroll-margin-bottom` below is what clears the bar.
    expect(card).not.toMatch(/margin-bottom/);
    const item = ruleBlock(moduleCss, `:global(${mobile}) .item.item`);
    expect(item).toMatch(
      /scroll-margin-bottom:\s*calc\(var\(--mobile-tab-bar-height\)\s*\+\s*var\(--safe-bottom\)\s*\+\s*var\(--nf-s3\)\)/,
    );
  });

  it("uses brand tokens only — no raw hex, no baked rgba, 4px max radius", () => {
    expect(moduleCss).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(moduleCss).not.toMatch(/rgba?\(/);
    const radii = Array.from(moduleCss.matchAll(/border-radius:\s*([^;]+);/g)).map((m) => m[1].trim());
    radii.forEach((r) => expect(r).toBe("4px"));
  });
});
