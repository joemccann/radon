import { describe, expect, it } from "vitest";
import { clusterPages } from "./cluster-pages";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const KEBAB_SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;

describe("cluster page registry", () => {
  it("registers all six cluster pages", () => {
    expect(clusterPages.map((page) => page.slug)).toEqual([
      "interactive-brokers-dark-pool-terminal",
      "defined-risk-options-structures",
      "fractional-kelly-position-sizing",
      "unusual-whales-interactive-brokers",
      "convex-options-from-dark-pool-flow",
      "crash-risk-index",
    ]);
  });

  it("keeps slugs unique, kebab-case, and off the homepage", () => {
    const slugs = clusterPages.map((page) => page.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
    for (const slug of slugs) {
      expect(slug).toMatch(KEBAB_SLUG);
    }
  });

  it("keeps every page's SEO surface within limits and unique", () => {
    const titles = clusterPages.map((page) => page.title);
    expect(new Set(titles).size).toBe(titles.length);
    for (const page of clusterPages) {
      expect(page.title.endsWith(" | Radon Terminal")).toBe(true);
      expect(page.title.length).toBeLessThanOrEqual(60);
      expect(page.description.length).toBeLessThanOrEqual(200);
      expect(page.description.length).toBeGreaterThan(50);
      expect(page.navLabel.length).toBeGreaterThan(0);
    }
  });

  it("freezes per-page lastModified as a content date, never request time", () => {
    for (const page of clusterPages) {
      expect(page.lastModified).toMatch(ISO_DATE);
      const lastModified = new Date(page.lastModified);
      expect(Number.isNaN(lastModified.getTime())).toBe(false);
      expect(lastModified.getTime()).toBeLessThanOrEqual(Date.now());
    }
  });
});
