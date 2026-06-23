/**
 * @vitest-environment jsdom
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { cleanup, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DashboardSurface from "../components/dashboard/DashboardSurface";

vi.mock("@/components/DashboardNewsFeed", () => ({
  default: () => React.createElement("div", { "data-testid": "mock-news-feed" }),
}));

vi.mock("../components/dashboard/PortfolioSnapshotCard", () => ({
  PortfolioSnapshotCard: () => React.createElement("div", { "data-testid": "mock-portfolio-card" }),
}));

vi.mock("../components/dashboard/OrdersSnapshotCard", () => ({
  OrdersSnapshotCard: () => React.createElement("div", { "data-testid": "mock-orders-card" }),
}));

vi.mock("../components/dashboard/OpportunitiesCard", () => ({
  OpportunitiesCard: () => React.createElement("div", { "data-testid": "mock-opportunities-card" }),
}));

const css = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");

function mediaBlock(query: string): string {
  const start = css.indexOf(`@media ${query}`);
  expect(start).toBeGreaterThanOrEqual(0);
  const open = css.indexOf("{", start);
  expect(open).toBeGreaterThan(start);
  let depth = 0;
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) return css.slice(open + 1, i);
    }
  }
  throw new Error(`unterminated media block for ${query}`);
}

function ruleBlock(source: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = source.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "m"));
  expect(match, `rule not found for ${selector}`).not.toBeNull();
  return match![1];
}

afterEach(() => {
  cleanup();
});

describe("dashboard mobile newsfeed layout", () => {
  it("labels Live Market Feed as the second dashboard section", () => {
    const { getByTestId } = render(
      <DashboardSurface portfolio={null} orders={null} realizedPnl={0} />,
    );

    expect(
      within(getByTestId("dashboard-section-portfolio")).getByRole("button").textContent,
    ).toMatch(/Portfolio\s*01/);
    expect(
      within(getByTestId("dashboard-section-news")).getByRole("button").textContent,
    ).toMatch(/Live Market Feed\s*02/);
    expect(
      within(getByTestId("dashboard-section-orders")).getByRole("button").textContent,
    ).toMatch(/Working & Filled\s*03/);
  });

  it("keeps the mobile visual order aligned with the section numbers", () => {
    const mobile = mediaBlock("(max-width: 720px)");

    expect(ruleBlock(mobile, ".dashboard-surface")).toMatch(/align-items:\s*stretch/);
    expect(ruleBlock(mobile, ".dashboard-surface__rail")).toMatch(/flex:\s*0\s+0\s+100%/);
    expect(ruleBlock(mobile, ".dashboard-surface__rail")).toMatch(/width:\s*100%/);
    expect(ruleBlock(mobile, ".dashboard-section,\n  .dashboard-section__body,\n  .dashboard-news,\n  .snapshot-card")).toMatch(/flex:\s*0\s+0\s+100%/);
    expect(ruleBlock(mobile, ".dashboard-section--portfolio")).toMatch(/order:\s*1/);
    expect(ruleBlock(mobile, ".dashboard-section--news")).toMatch(/order:\s*2/);
    expect(ruleBlock(mobile, ".dashboard-section--orders")).toMatch(/order:\s*3/);
    expect(ruleBlock(mobile, ".dashboard-section--opportunities")).toMatch(/order:\s*4/);
    expect(ruleBlock(mobile, ".dashboard-section--flow-surprise")).toMatch(/order:\s*5/);
    expect(ruleBlock(mobile, ".dashboard-section--catalysts")).toMatch(/order:\s*6/);
  });

  it("never leads the mobile dashboard with Flow Surprise", () => {
    const mobile = mediaBlock("(max-width: 720px)");

    // `.dashboard-surface__main` is `display: contents`, so every section becomes
    // a flex child of `.dashboard-surface`. A section without an explicit order
    // defaults to flex order 0 and sorts ABOVE the portfolio snapshot (order 1) —
    // which is exactly how Flow Surprise once floated to the top on mobile.
    const orderOf = (modifier: string): number => {
      const match = ruleBlock(mobile, `.dashboard-section--${modifier}`).match(/order:\s*(\d+)/);
      expect(match, `order missing for ${modifier}`).not.toBeNull();
      return Number(match![1]);
    };

    const portfolioOrder = orderOf("portfolio");
    const flowSurpriseOrder = orderOf("flow-surprise");

    expect(portfolioOrder).toBe(1);
    expect(flowSurpriseOrder).toBeGreaterThan(portfolioOrder);
    // Flow Surprise sits below the primary actionable surfaces, matching desktop intent.
    expect(flowSurpriseOrder).toBeGreaterThan(orderOf("orders"));
    expect(flowSurpriseOrder).toBeGreaterThan(orderOf("opportunities"));
  });

  it("stacks the mobile news header actions so Refresh stays inside the panel border", () => {
    expect(ruleBlock(css, ".news-feed-updated")).toMatch(/border-bottom:\s*0/);

    const mobileNewsHeader = ruleBlock(
      css,
      'body[data-mobile="true"] .dashboard-news .section-header',
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
