import { describe, expect, it } from "vitest";
import { siteUrl } from "../seo";
import {
  PAGE_DESCRIPTION,
  PAGE_HEADLINE,
  PAGE_NAV_LABEL,
  PAGE_SLUG,
  PAGE_TITLE,
  breadcrumbStructuredData,
  kellyFaqStructuredData,
  kellyPageMetadata,
  kellySizingFaqEntries,
  pageStructuredData,
  pageUrl,
  techArticleStructuredData,
  workedExampleRows,
} from "./fractional-kelly-position-sizing";

const AI_CLICHES = /seamless|unleash|elevate|game-changer|next-gen/i;
const EM_OR_EN_DASH = /[–—]/;

describe("fractional Kelly page content contract", () => {
  it("keeps the title and description within SEO limits", () => {
    expect(PAGE_TITLE.length).toBeLessThanOrEqual(60);
    expect(PAGE_TITLE.endsWith(" | Radon Terminal")).toBe(true);
    expect(PAGE_DESCRIPTION.length).toBeLessThanOrEqual(160);
  });

  it("bans em dashes and AI cliches from the copy", () => {
    const corpus = [
      PAGE_TITLE,
      PAGE_DESCRIPTION,
      PAGE_HEADLINE,
      ...workedExampleRows.flatMap((row) => [row.step, row.value]),
      ...kellySizingFaqEntries.flatMap((entry) => [
        entry.question,
        entry.answer,
      ]),
    ].join(" ");
    expect(corpus).not.toMatch(EM_OR_EN_DASH);
    expect(corpus).not.toMatch(AI_CLICHES);
  });

  it("keeps every FAQ entry a real question with a substantive answer", () => {
    expect(kellySizingFaqEntries).toHaveLength(4);
    for (const entry of kellySizingFaqEntries) {
      expect(entry.question.trim().endsWith("?")).toBe(true);
      expect(entry.answer.trim().length).toBeGreaterThan(80);
    }
  });

  it("states the cap as a ceiling that cannot be overridden", () => {
    const corpus = kellySizingFaqEntries
      .map((entry) => entry.answer)
      .join(" ");
    expect(corpus).toContain("ceiling, not a target");
    expect(corpus).toContain("2.5%");
    expect(corpus).not.toMatch(/cap (can|may) be overridden/i);
  });

  it("derives the worked example arithmetic correctly", () => {
    const b = 1500 / 500;
    const p = 0.45;
    const fullKelly = p - (1 - p) / b;
    expect(b).toBe(3);
    expect(fullKelly).toBeCloseTo(0.2667, 4);
    expect(fullKelly / 4).toBeCloseTo(0.0667, 4);
    const corpus = workedExampleRows.map((row) => row.value).join(" ");
    expect(corpus).toContain("3.0");
    expect(corpus).toContain("26.7%");
    expect(corpus).toContain("6.7%");
    expect(corpus).toContain("2.5%");
  });

  it("generates FAQPage schema that mirrors the visible entries exactly", () => {
    const schema = kellyFaqStructuredData();
    expect(schema["@type"]).toBe("FAQPage");
    expect(schema.mainEntity).toHaveLength(kellySizingFaqEntries.length);
    schema.mainEntity.forEach((question, index) => {
      expect(question.name).toBe(kellySizingFaqEntries[index].question);
      expect(question.acceptedAnswer.text).toBe(
        kellySizingFaqEntries[index].answer,
      );
    });
  });

  it("generates a BreadcrumbList that mirrors the visible breadcrumb", () => {
    const schema = breadcrumbStructuredData();
    expect(schema["@type"]).toBe("BreadcrumbList");
    expect(schema.itemListElement).toEqual([
      { "@type": "ListItem", position: 1, name: "Radon", item: siteUrl },
      {
        "@type": "ListItem",
        position: 2,
        name: PAGE_NAV_LABEL,
        item: `${siteUrl}/${PAGE_SLUG}`,
      },
    ]);
  });

  it("generates a TechArticle that mirrors the h1 and meta description", () => {
    const schema = techArticleStructuredData();
    expect(schema["@type"]).toBe("TechArticle");
    expect(schema.headline).toBe(PAGE_HEADLINE);
    expect(schema.description).toBe(PAGE_DESCRIPTION);
    expect(schema.url).toBe(pageUrl);
    expect(schema.mainEntityOfPage).toBe(pageUrl);
    expect(schema.datePublished).toBe("2026-07-03");
    expect(schema.author).toEqual({
      "@type": "Organization",
      name: "Radon",
      url: siteUrl,
    });
  });

  it("emits exactly BreadcrumbList, TechArticle, and FAQPage", () => {
    const types = pageStructuredData().map(
      (schema) => (schema as { "@type": string })["@type"],
    );
    expect(types).toEqual(["BreadcrumbList", "TechArticle", "FAQPage"]);
  });

  it("builds page metadata with canonical, OG article, and shared image", () => {
    const metadata = kellyPageMetadata();
    expect(metadata.alternates?.canonical).toBe(`/${PAGE_SLUG}`);
    const openGraph = metadata.openGraph as {
      type: string;
      url: string;
      images: Array<{ url: string }>;
    };
    expect(openGraph.type).toBe("article");
    expect(openGraph.url).toBe(`/${PAGE_SLUG}`);
    expect(openGraph.images[0].url).toBe("/og-image.png");
    const twitter = metadata.twitter as { card: string; images: string[] };
    expect(twitter.card).toBe("summary_large_image");
    expect(twitter.images[0]).toBe("/og-image.png");
  });
});
