/**
 * E2E — mobile newsfeed item layout invariants at 393x852.
 *
 * Item G of the review: the rebuilt mobile news item was verified entirely with
 * throwaway scripts in /tmp, so not one of its load-bearing numbers was pinned
 * in CI. This spec is that coverage. It pins the invariants the layout exists
 * to deliver (spec: docs/mobile-newsfeed-layout.md) plus the three defects that
 * only reproduce in a real engine: the safe-area seam (B), chip size parity
 * (E), and the focus drop on the +N expander (F).
 *
 * Everything is measured against a stubbed feed so the numbers are
 * deterministic; nothing here depends on what the scraper happened to capture.
 */

import { test, expect, type Page } from "@playwright/test";

const TAGS = ["SPX", "VOLATILITY", "OPTIONS", "MACRO", "DEALER-GAMMA", "SKEW"];

const SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">' +
  '<rect width="1200" height="675" fill="#0b0e13"/></svg>';

const POSTS = [
  {
    id: "layout-1",
    title: "Dealer gamma flips negative into the September expiry",
    content:
      "Short-dated SPX realized vol remains pinned near the floor, supported by heavy overwriting and a dealer book that is long gamma above spot.",
    timestamp: new Date(Date.now() - 26 * 60_000).toISOString(),
    images: ["/layout-chart.svg"],
    raw_images: ["/layout-chart.svg"],
    tags: TAGS,
  },
  {
    id: "layout-2",
    title: "Text-only note: the Fed volatility trade",
    content: "No chart on this one. Positioning has become materially cleaner.",
    timestamp: new Date(Date.now() - 3 * 3600_000).toISOString(),
    images: [] as string[],
    raw_images: [] as string[],
    tags: TAGS.slice(0, 3),
  },
  {
    id: "layout-3",
    title: "Credit spreads",
    content: "One-line body.",
    timestamp: new Date(Date.now() - 2 * 86400_000).toISOString(),
    images: ["/layout-chart.svg"],
    raw_images: ["/layout-chart.svg"],
    tags: TAGS.slice(0, 5),
  },
];

async function stubFeed(page: Page) {
  await page.route("**/api/newsfeed/posts**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(POSTS),
    }),
  );
  const svg = { status: 200, contentType: "image/svg+xml", body: SVG };
  await page.route("**/_next/image**", (route) => route.fulfill(svg));
  await page.route("**/layout-chart.svg", (route) => route.fulfill(svg));
}

/**
 * Effective hit area of a control whose visual box is deliberately smaller than
 * its target: the union of the layout box and its ::after pad, measured the way
 * a finger experiences it rather than the way the box model declares it. The
 * pad is centred on the control either way (a translate(-50%,-50%) 44px square,
 * or a symmetric negative `inset`), so its used width/height IS the hit extent.
 *
 * Inlined into each page.evaluate rather than shared through eval/new Function,
 * which the app's CSP would reject.
 */
type HitArea = { height: number; width: number; boxHeight: number };

test.describe("mobile newsfeed item layout (393x852)", () => {
  test.beforeEach(async ({ page }) => {
    await stubFeed(page);
    await page.goto("/dashboard");
    await expect(page.getByTestId("mobile-tab-bar")).toBeVisible();
    await expect(page.locator("li.news-feed-item").first()).toBeVisible();
  });

  test("tag strip is a single 24px row with a +N affordance", async ({ page }) => {
    const tags = page.locator("li.news-feed-item").first().locator("div.news-feed-tags");
    const box = await tags.boundingBox();
    expect(box).not.toBeNull();
    // One row of 24px chips. Two rows would be 56px (24 + 8 gap + 24).
    expect(box!.height).toBeLessThanOrEqual(32);

    const more = tags.getByRole("button", { name: /more tags/i });
    await expect(more).toBeVisible();
    await expect(more).toHaveText("+2");

    // Exactly four chips plus the +N are laid out; the rest are display:none.
    const visibleChips = await tags.evaluate((el) =>
      Array.from(el.querySelectorAll("button.news-feed-tag-chip")).filter(
        (chip) => getComputedStyle(chip).display !== "none",
      ).length,
    );
    expect(visibleChips).toBe(5);
  });

  test("footer's three children share a baseline within 1px", async ({ page }) => {
    const centres = await page.locator("li.news-feed-item").first().evaluate((item) => {
      const footer = item.querySelector(".news-feed-footer")!;
      const pill = footer.querySelector(".news-feed-link-pill")!;
      const stamp = footer.querySelector(".news-feed-timestamp")!;
      const star = footer.querySelector(".star-toggle")!;
      const centre = (el: Element) => {
        const r = el.getBoundingClientRect();
        return r.top + r.height / 2;
      };
      return {
        pill: centre(pill),
        stamp: centre(stamp),
        star: centre(star),
        footerHeight: footer.getBoundingClientRect().height,
      };
    });
    expect(Math.abs(centres.pill - centres.stamp)).toBeLessThanOrEqual(1);
    expect(Math.abs(centres.stamp - centres.star)).toBeLessThanOrEqual(1);
    // One 24px row, not the old 44px row.
    expect(centres.footerHeight).toBeLessThanOrEqual(28);
  });

  test("timestamp prints one line with no orphaned fragment", async ({ page }) => {
    const stamp = await page.locator("li.news-feed-item").first().evaluate((item) => {
      const el = item.querySelector(".news-feed-timestamp") as HTMLElement;
      const compact = Array.from(el.children).find(
        (child) => getComputedStyle(child).display !== "none",
      ) as HTMLElement;
      return {
        rects: el.getClientRects().length,
        height: el.getBoundingClientRect().height,
        whiteSpace: getComputedStyle(el).whiteSpace,
        visibleText: compact?.textContent?.trim() ?? "",
        renderedTexts: Array.from(el.children)
          .filter((child) => getComputedStyle(child).display !== "none")
          .map((child) => child.textContent?.trim() ?? ""),
      };
    });
    expect(stamp.rects).toBe(1);
    expect(stamp.height).toBeLessThanOrEqual(26);
    expect(stamp.whiteSpace).toBe("nowrap");
    // Exactly one of the two stamp variants renders — never both.
    expect(stamp.renderedTexts).toHaveLength(1);
    expect(stamp.visibleText).toMatch(/ago$/i);
  });

  test("no horizontal overflow anywhere on the dashboard", async ({ page }) => {
    const overflow = await page.evaluate(() => {
      const doc = document.documentElement;
      const offenders: string[] = [];
      document.querySelectorAll<HTMLElement>(".dashboard-news *").forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.right > doc.clientWidth + 1) {
          offenders.push(`${el.tagName.toLowerCase()}.${el.className}`.slice(0, 120));
        }
      });
      return {
        scrollWidth: doc.scrollWidth,
        clientWidth: doc.clientWidth,
        offenders: offenders.slice(0, 5),
      };
    });
    expect(overflow.offenders).toEqual([]);
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1);
  });

  test("item padding is inline-zero so the rule spans the card interior", async ({ page }) => {
    const padding = await page
      .locator("li.news-feed-item")
      .first()
      .evaluate((el) => {
        const cs = getComputedStyle(el);
        return { left: cs.paddingLeft, right: cs.paddingRight, top: cs.paddingTop };
      });
    expect(padding.left).toBe("0px");
    expect(padding.right).toBe("0px");
    expect(padding.top).toBe("16px");
  });

  test("scrolling the last item into view clears the fixed tab bar", async ({ page }) => {
    const clearance = await page.evaluate(async () => {
      const items = document.querySelectorAll("li.news-feed-item");
      const last = items[items.length - 1] as HTMLElement;
      last.scrollIntoView({ block: "end", behavior: "auto" });
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      const bar = document.querySelector(".mobile-tab-bar")!.getBoundingClientRect();
      const rect = last.getBoundingClientRect();
      return { itemBottom: rect.bottom, barTop: bar.top };
    });
    expect(clearance.itemBottom).toBeLessThanOrEqual(clearance.barTop + 1);
  });

  test("every shrunk control keeps its effective tap target", async ({ page }) => {
    const areas: Record<"refresh" | "linkPill" | "star" | "tagChip", HitArea> =
      await page.evaluate(() => {
        function hit(el: Element) {
          const box = el.getBoundingClientRect();
          const after = getComputedStyle(el, "::after");
          const cx = box.left + box.width / 2;
          const cy = box.top + box.height / 2;
          let height = box.height;
          let width = box.width;
          if (after.content !== "none") {
            const w = parseFloat(after.width);
            const h = parseFloat(after.height);
            if (Number.isFinite(w) && Number.isFinite(h)) {
              height = Math.max(box.bottom, cy + h / 2) - Math.min(box.top, cy - h / 2);
              width = Math.max(box.right, cx + w / 2) - Math.min(box.left, cx - w / 2);
            }
          }
          return { height, width, boxHeight: box.height };
        }
        const item = document.querySelector("li.news-feed-item")!;
        const pick = (sel: string, root: ParentNode = document) => root.querySelector(sel)!;
        return {
          refresh: hit(pick(".dashboard-news .news-feed-refresh")),
          linkPill: hit(pick(".news-feed-link-pill", item)),
          star: hit(pick(".star-toggle", item)),
          tagChip: hit(pick("button.news-feed-tag-chip", item)),
        };
      });

    // 44px floor on the primary controls, box deliberately smaller.
    for (const key of ["refresh", "linkPill", "star"] as const) {
      expect(areas[key].height, `${key} hit height`).toBeGreaterThanOrEqual(44);
      expect(areas[key].width, `${key} hit width`).toBeGreaterThanOrEqual(44);
    }
    // ⛔ RECORDED EXCEPTION to the 44px floor, ACCEPTED by the operator
    // 2026-08-16. This chip is 32px because a 44px pad on a 24px chip overlaps
    // the adjacent wrapped row and the footer's link pill (see
    // DashboardNewsFeed.module.css), and honouring the floor would mean a
    // taller tag strip, which is the orphan-chip bug this work removed.
    // Bounded both ways so the accepted value cannot silently drift.
    expect(areas.tagChip.boxHeight).toBeGreaterThanOrEqual(24);
    expect(areas.tagChip.height, "tag chip hit height (32px exception)").toBeGreaterThanOrEqual(32);
    expect(areas.tagChip.height, "exception is 32px, not silently widened").toBeLessThan(44);
  });
});

test.describe("defect B — the safe-area inset must not open a mid-page hole", () => {
  /**
   * `.card { margin-bottom: calc(var(--safe-bottom) + 12px) }` assumed the feed
   * card ends the page. It does not: `.dashboard-surface__right` follows it
   * inside a grid that already supplies its own 16px gap. The inset is spent
   * mid-scroll, so on a notched iPhone the seam between Feed / 01 and
   * Top Candidates / 02 blows out to 62px while every other seam stays at 16.
   */
  async function seams(page: Page) {
    return page.evaluate(() => {
      const rect = (id: string) =>
        document.querySelector(`[data-testid="${id}"]`)!.getBoundingClientRect();
      const feed = rect("dashboard-section-feed");
      const signals = rect("dashboard-section-signals");
      const catalysts = rect("dashboard-section-catalysts");
      const engine = rect("dashboard-section-engine");
      return {
        feedToSignals: signals.top - feed.bottom,
        signalsToCatalysts: catalysts.top - signals.bottom,
        catalystsToEngine: engine.top - catalysts.bottom,
      };
    });
  }

  test("feed seam matches every other section seam with a 0px inset", async ({ page }) => {
    await stubFeed(page);
    await page.goto("/dashboard");
    await expect(page.locator("li.news-feed-item").first()).toBeVisible();

    const s = await seams(page);
    expect(Math.abs(s.feedToSignals - s.signalsToCatalysts)).toBeLessThanOrEqual(1);
    expect(Math.abs(s.feedToSignals - s.catalystsToEngine)).toBeLessThanOrEqual(1);
  });

  test("feed seam matches every other section seam with a 34px notch inset", async ({ page }) => {
    await stubFeed(page);
    await page.goto("/dashboard");
    await expect(page.locator("li.news-feed-item").first()).toBeVisible();

    // Simulate an iPhone home-indicator inset. env(safe-area-inset-bottom) is
    // not settable from a test, but every consumer reads the --safe-bottom
    // token, so overriding the token is the faithful simulation.
    await page.addStyleTag({ content: ":root { --safe-bottom: 34px !important; }" });
    await page.evaluate(
      () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))),
    );

    const s = await seams(page);
    expect(Math.abs(s.feedToSignals - s.signalsToCatalysts)).toBeLessThanOrEqual(1);
    expect(Math.abs(s.feedToSignals - s.catalystsToEngine)).toBeLessThanOrEqual(1);
  });
});

test.describe("defect E — one chip size on screen", () => {
  test("tag-bar chip and item chip render the same height and both stay tappable", async ({
    page,
  }) => {
    await stubFeed(page);
    // Deep-link the filter so the tag bar renders with an active chip.
    await page.goto("/dashboard?tags=SPX");
    await expect(page.locator(".news-feed-tag-bar-chip").first()).toBeVisible();
    await expect(page.locator("li.news-feed-item").first()).toBeVisible();

    const sizes = await page.evaluate(() => {
      function hit(el: Element) {
        const box = el.getBoundingClientRect();
        const after = getComputedStyle(el, "::after");
        const cx = box.left + box.width / 2;
        const cy = box.top + box.height / 2;
        let height = box.height;
        let width = box.width;
        if (after.content !== "none") {
          const w = parseFloat(after.width);
          const h = parseFloat(after.height);
          if (Number.isFinite(w) && Number.isFinite(h)) {
            height = Math.max(box.bottom, cy + h / 2) - Math.min(box.top, cy - h / 2);
            width = Math.max(box.right, cx + w / 2) - Math.min(box.left, cx - w / 2);
          }
        }
        return { height, width, boxHeight: box.height };
      }
      const barChip = document.querySelector(".news-feed-tag-bar-chip")!;
      const itemChip = document.querySelector("li.news-feed-item button.news-feed-tag-chip")!;
      return {
        bar: { ...hit(barChip), text: barChip.textContent?.trim() },
        item: { ...hit(itemChip), text: itemChip.textContent?.trim() },
      };
    });

    // Same label, two rows apart — they must not be two different sizes.
    expect(sizes.bar.text).toContain("SPX");
    expect(sizes.item.text).toContain("SPX");
    expect(Math.abs(sizes.bar.boxHeight - sizes.item.boxHeight)).toBeLessThanOrEqual(1);
    // Whichever one shrank keeps a real target.
    expect(sizes.bar.height).toBeGreaterThanOrEqual(44);
    expect(sizes.bar.width).toBeGreaterThanOrEqual(44);
  });
});

test.describe("defect F — the +N expander must not drop focus", () => {
  test("focus stays on a real control after the expander activates", async ({ page }) => {
    await stubFeed(page);
    await page.goto("/dashboard");
    await expect(page.locator("li.news-feed-item").first()).toBeVisible();

    const more = page
      .locator("li.news-feed-item")
      .first()
      .getByRole("button", { name: /more tags/i });
    await more.focus();
    await expect(more).toBeFocused();

    await more.click();

    const after = await page.evaluate(() => {
      const active = document.activeElement as HTMLElement | null;
      const tagList = document.querySelector("li.news-feed-item div.news-feed-tags");
      return {
        tag: active?.tagName ?? null,
        text: active?.textContent?.trim().slice(0, 40) ?? null,
        visible: active ? getComputedStyle(active).display !== "none" : false,
        insideTagList: !!(active && tagList && tagList.contains(active)),
        // A control the user can no longer reach cannot carry a meaningful
        // disclosure state; `display: none` + aria-expanded="true" is inert.
        inertExpanded: Array.from(document.querySelectorAll("[aria-expanded]")).some(
          (el) => getComputedStyle(el).display === "none",
        ),
      };
    });

    // Measured today: activeElement is <main>. The button was hidden while
    // focused, so the browser walked focus up to the nearest focusable
    // container — functionally the same loss of place as landing on <body>,
    // and the reason a bare `!== BODY` check is not the right assertion.
    expect(after.tag).not.toBe("BODY");
    expect(after.tag).toBe("BUTTON");
    expect(after.insideTagList).toBe(true);
    expect(after.visible).toBe(true);
    expect(after.inertExpanded).toBe(false);
  });
});
