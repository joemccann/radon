/**
 * @vitest-environment jsdom
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { cleanup, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DashboardSurface from "../components/dashboard/DashboardSurface";
import { DASHBOARD_SECTION_IDS } from "../lib/useDashboardSectionVisibility";

vi.mock("@/components/DashboardNewsFeed", () => ({
  default: () => React.createElement("div", { "data-testid": "mock-news-feed" }),
}));

vi.mock("../components/dashboard/ScannerHero", () => ({
  default: () => React.createElement("div", { "data-testid": "mock-scanner-hero" }),
}));

vi.mock("../components/dashboard/CatalystsQuadrant", () => ({
  default: () => React.createElement("div", { "data-testid": "mock-catalysts-quadrant" }),
}));

vi.mock("../components/dashboard/EngineStatePanel", () => ({
  default: () => React.createElement("div", { "data-testid": "mock-engine-panel" }),
}));

const css = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");
const STORAGE_KEY = "radon:mobile-dashboard:hidden-sections";

function resetLocalStorage(): void {
  if (
    typeof window.localStorage?.clear === "function" &&
    typeof window.localStorage?.getItem === "function" &&
    typeof window.localStorage?.setItem === "function"
  ) {
    window.localStorage.clear();
    return;
  }

  const store = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => store.set(key, String(value)),
      removeItem: (key: string) => store.delete(key),
      clear: () => store.clear(),
    },
  });
}

function mediaBlocks(query: string): string[] {
  const blocks: string[] = [];
  let from = 0;
  for (;;) {
    const start = css.indexOf(`@media ${query}`, from);
    if (start === -1) break;
    const open = css.indexOf("{", start);
    expect(open).toBeGreaterThan(start);
    let depth = 0;
    for (let i = open; i < css.length; i += 1) {
      if (css[i] === "{") depth += 1;
      if (css[i] === "}") {
        depth -= 1;
        if (depth === 0) {
          blocks.push(css.slice(open + 1, i));
          from = i;
          break;
        }
      }
    }
    if (from <= start) break;
  }
  expect(blocks.length).toBeGreaterThan(0);
  return blocks;
}

/** The media block (for `query`) that contains `selector`. */
function mediaBlock(query: string, selector: string): string {
  const block = mediaBlocks(query).find((b) => b.includes(selector));
  expect(block, `no @media ${query} block contains ${selector}`).toBeDefined();
  return block!;
}

function ruleBlock(source: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = source.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "m"));
  expect(match, `rule not found for ${selector}`).not.toBeNull();
  return match![1];
}

beforeEach(() => {
  resetLocalStorage();
});

afterEach(() => {
  cleanup();
});

describe("dashboard mobile newsfeed layout", () => {
  it("assigns dashboard section counts and keeps collapse affordances", () => {
    const { getByTestId } = render(
      <DashboardSurface portfolio={null} realizedPnl={0} marketState={null} />,
    );

    // Open desktop: outer chrome is collapse-only (count + chevron); the
    // snapshot card owns the mount label (skill-stack T2).
    expect(
      within(getByTestId("dashboard-section-feed")).getByRole("button").textContent,
    ).toMatch(/01/);
    expect(
      within(getByTestId("dashboard-section-signals")).getByRole("button").textContent,
    ).toMatch(/02/);
    expect(
      within(getByTestId("dashboard-section-catalysts")).getByRole("button").textContent,
    ).toMatch(/03/);
    expect(
      within(getByTestId("dashboard-section-engine")).getByRole("button").textContent,
    ).toMatch(/04/);

    expect(
      within(getByTestId("dashboard-section-feed")).getByRole("button").getAttribute("aria-label"),
    ).toBe("Live Market Feed");
    expect(
      within(getByTestId("dashboard-section-signals")).getByRole("button").getAttribute("aria-label"),
    ).toBe("Top Candidates");
  });

  it("fails open when persisted state hides every dashboard section", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(DASHBOARD_SECTION_IDS));

    render(
      <DashboardSurface portfolio={null} realizedPnl={0} marketState={null} />,
    );

    await waitFor(() => {
      expect(document.getElementById("dashboard-section-body-feed")?.hasAttribute("hidden")).toBe(false);
      expect(document.getElementById("dashboard-section-body-signals")?.hasAttribute("hidden")).toBe(false);
      expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY)!)).toEqual([]);
    });
  });

  it("keeps the DOM order feed → signals → catalysts → engine so mobile stacking needs no order rules", () => {
    const { container } = render(
      <DashboardSurface portfolio={null} realizedPnl={0} marketState={null} />,
    );

    const sections = Array.from(container.querySelectorAll("[data-testid^='dashboard-section-']")).map(
      (el) => el.getAttribute("data-testid"),
    );
    expect(sections).toEqual([
      "dashboard-section-feed",
      "dashboard-section-signals",
      "dashboard-section-catalysts",
      "dashboard-section-engine",
    ]);
  });

  it("collapses the body grid to one column below 1280 and the KPI strip to two below 720", () => {
    const tablet = mediaBlock("(max-width: 1280px)", ".dashboard-surface__grid");
    expect(ruleBlock(tablet, ".dashboard-surface__grid")).toMatch(/grid-template-columns:\s*1fr/);

    const mobile = mediaBlock("(max-width: 720px)", ".dashboard-kpi");
    expect(ruleBlock(mobile, ".dashboard-kpi")).toMatch(/repeat\(2,/);
  });

  it("stacks the mobile news header actions so Refresh stays inside the panel border", () => {
    expect(ruleBlock(css, ".news-feed-updated")).toMatch(/border-bottom:\s*0/);

    // Instrument header (T3) — mobile stacks the device label + rail control.
    const mobileNewsHeader = ruleBlock(
      css,
      'body[data-mobile="true"] .dashboard-news__header',
    );
    expect(mobileNewsHeader).toMatch(/flex-direction:\s*column/);
    expect(mobileNewsHeader).toMatch(/align-items:\s*stretch/);

    const mobileActions = ruleBlock(
      css,
      'body[data-mobile="true"] .dashboard-news .news-feed-actions',
    );
    expect(mobileActions).toMatch(/width:\s*100%/);
    expect(mobileActions).toMatch(/min-width:\s*0/);

    const mobileRefresh = ruleBlock(
      css,
      'body[data-mobile="true"] .dashboard-news .news-feed-refresh',
    );
    expect(mobileRefresh).toMatch(/flex:\s*0\s+0\s+auto/);
    expect(mobileRefresh).toMatch(/min-height:\s*44px/);
  });
});
