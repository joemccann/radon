import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "../app/globals.css"), "utf8");

function ruleBlock(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`, "m"));
  expect(match, `rule not found for ${selector}`).not.toBeNull();
  return match![1];
}

describe("typography readability contracts", () => {
  it("gives sustained prose a readable size, rhythm, and measure", () => {
    expect(ruleBlock(".news-feed-summary")).toMatch(/font-size:\s*var\(--text-prose\)/);
    expect(ruleBlock(".news-feed-summary")).toMatch(/line-height:\s*1\.6/);
    expect(ruleBlock(".news-feed-summary")).toMatch(/max-inline-size:\s*70ch/);
    expect(ruleBlock(".news-feed-summary")).toMatch(/text-wrap:\s*pretty/);

    expect(ruleBlock(".chat-markdown p")).toMatch(/font-size:\s*var\(--text-prose\)/);
    expect(ruleBlock(".chat-markdown p")).toMatch(/line-height:\s*1\.55/);
    expect(ruleBlock(".chat-markdown-list-item")).toMatch(/font-size:\s*var\(--text-prose\)/);
    expect(ruleBlock(".chat-line")).toMatch(/font-size:\s*var\(--text-prose\)/);
    expect(ruleBlock(".demo-welcome-lead")).toMatch(/font-size:\s*var\(--text-prose\)/);
    expect(ruleBlock(".demo-welcome-note")).toMatch(/font-size:\s*var\(--text-prose\)/);
    expect(ruleBlock(".company-description p")).toMatch(/font-size:\s*var\(--text-prose\)/);
  });

  it("balances display headings without compressing their line boxes", () => {
    expect(ruleBlock(".watchlist-hero h1,\n.watchlist-empty h1")).toMatch(
      /line-height:\s*1\.08/,
    );
    expect(ruleBlock(".watchlist-hero h1,\n.watchlist-empty h1")).toMatch(
      /text-wrap:\s*balance/,
    );
    expect(ruleBlock(".news-feed-headline")).toMatch(/text-wrap:\s*balance/);
    expect(ruleBlock(".section-title")).toMatch(/line-height:\s*1\.1/);
    expect(ruleBlock(".section-title")).toMatch(/text-wrap:\s*balance/);
  });

  it("keeps order controls and table headers above the operational text floor", () => {
    for (const selector of [
      "th",
      ".pill",
      ".actions-th",
      ".orders-command-strip__label",
      ".order-status-pill",
      ".modify-market-label",
      ".modify-rth-hint",
    ]) {
      expect(ruleBlock(selector)).toMatch(/font-size:\s*var\(--text-meta\)/);
    }
    for (const selector of [".btn-order-action", ".modify-price-label"]) {
      expect(ruleBlock(selector)).toMatch(/font-size:\s*var\(--text-label\)/);
    }
  });

  it("never irreversibly truncates service-health details", () => {
    expect(ruleBlock(".service-health-banner")).toMatch(/align-items:\s*flex-start/);
    expect(ruleBlock(".service-health-banner__message")).toMatch(/white-space:\s*normal/);
    expect(ruleBlock(".service-health-banner__message")).toMatch(/overflow-wrap:\s*anywhere/);
    expect(ruleBlock(".service-health-banner__dormant")).toMatch(/white-space:\s*normal/);
  });

  it("prevents iOS focus zoom for every date and time control", () => {
    for (const type of ["date", "datetime-local", "time", "month"]) {
      expect(css).toContain(`body[data-mobile="true"] input[type="${type}"]`);
    }
  });

  it("uses explicit placeholder and text-detail styling", () => {
    expect(ruleBlock("::placeholder")).toMatch(/opacity:\s*1/);
    expect(ruleBlock("::selection")).toMatch(/background:/);
    expect(ruleBlock("a")).toMatch(/text-decoration-skip-ink:\s*auto/);
  });
});
