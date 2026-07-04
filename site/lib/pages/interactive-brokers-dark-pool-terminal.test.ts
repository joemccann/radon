import { describe, expect, it } from "vitest";
import {
  CLUSTER_SLUG,
  NAV_LABEL,
  PAGE_DATE_MODIFIED,
  PAGE_DATE_PUBLISHED,
  PAGE_DESCRIPTION,
  PAGE_HEADLINE,
  PAGE_TITLE,
  breadcrumbStructuredData,
  faqEntries,
  faqStructuredData,
  ibRoles,
  pageMetadata,
  pageStructuredData,
  requirementRows,
  techArticleStructuredData,
} from "./interactive-brokers-dark-pool-terminal";
import { SITE_NAME, siteUrl } from "../seo";

const EM_OR_EN_DASH = /[–—]/;
const AI_CLICHES = /seamless|unleash|elevate|game-changer|next-gen/i;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

describe("interactive-brokers-dark-pool-terminal content contract", () => {
  it("keeps the SEO surface within limits", () => {
    expect(PAGE_TITLE.length).toBeLessThanOrEqual(60);
    expect(PAGE_TITLE.endsWith(" | Radon Terminal")).toBe(true);
    expect(PAGE_DESCRIPTION.length).toBeLessThanOrEqual(160);
  });

  it("bans em and en dashes and AI cliches from the copy", () => {
    const corpus = [
      PAGE_TITLE,
      PAGE_DESCRIPTION,
      PAGE_HEADLINE,
      ...faqEntries.flatMap((entry) => [entry.question, entry.answer]),
      ...ibRoles.flatMap((row) => [row.role, row.supplies, row.terminalUse]),
      ...requirementRows.flatMap((row) => [row.item, row.liveUse, row.demo]),
    ].join(" ");
    expect(corpus).not.toMatch(EM_OR_EN_DASH);
    expect(corpus).not.toMatch(AI_CLICHES);
  });

  it("is honest about the dark pool source and broker scope", () => {
    const corpus = faqEntries.map((entry) => entry.answer).join(" ");
    expect(corpus).toContain("Unusual Whales");
    expect(corpus).toContain("Interactive Brokers");
    expect(corpus).toContain("demo.radon.run");
    expect(faqEntries[0].answer.startsWith("No.")).toBe(true);
    expect(faqEntries[3].answer.startsWith("No.")).toBe(true);
  });

  it("keeps every FAQ entry a real question with a substantive answer", () => {
    expect(faqEntries).toHaveLength(4);
    for (const entry of faqEntries) {
      expect(entry.question.trim().endsWith("?")).toBe(true);
      expect(entry.answer.trim().length).toBeGreaterThan(80);
    }
  });

  it("generates FAQPage schema that mirrors the visible entries exactly", () => {
    const schema = faqStructuredData();
    expect(schema["@type"]).toBe("FAQPage");
    expect(schema.mainEntity).toHaveLength(faqEntries.length);
    schema.mainEntity.forEach((question, index) => {
      expect(question.name).toBe(faqEntries[index].question);
      expect(question.acceptedAnswer.text).toBe(faqEntries[index].answer);
    });
  });

  it("mirrors the breadcrumb schema to the visible breadcrumb", () => {
    const schema = breadcrumbStructuredData();
    expect(schema.itemListElement).toEqual([
      { "@type": "ListItem", position: 1, name: "Radon", item: siteUrl },
      {
        "@type": "ListItem",
        position: 2,
        name: NAV_LABEL,
        item: `${siteUrl}/${CLUSTER_SLUG}`,
      },
    ]);
  });

  it("pins the TechArticle schema to the h1 and frozen dates", () => {
    const schema = techArticleStructuredData();
    expect(schema.headline).toBe(PAGE_HEADLINE);
    expect(schema.description).toBe(PAGE_DESCRIPTION);
    expect(schema.url).toBe(`${siteUrl}/${CLUSTER_SLUG}`);
    expect(schema.mainEntityOfPage).toBe(schema.url);
    expect(schema.author).toEqual({
      "@type": "Organization",
      name: "Radon",
      url: siteUrl,
    });
    expect(schema.publisher).toEqual(schema.author);
    expect(PAGE_DATE_PUBLISHED).toMatch(ISO_DATE);
    expect(PAGE_DATE_MODIFIED).toMatch(ISO_DATE);
    expect(schema.isPartOf).toEqual({
      "@type": "WebSite",
      name: SITE_NAME,
      url: siteUrl,
    });
  });

  it("emits exactly BreadcrumbList, TechArticle, and FAQPage", () => {
    expect(pageStructuredData().map((entry) => entry["@type"])).toEqual([
      "BreadcrumbList",
      "TechArticle",
      "FAQPage",
    ]);
  });

  it("re-includes the shared OG image because metadata merge is shallow", () => {
    const metadata = pageMetadata();
    expect(metadata.alternates?.canonical).toBe(`/${CLUSTER_SLUG}`);
    expect(metadata.openGraph?.images).toBeTruthy();
    expect(metadata.twitter?.images).toBeTruthy();
  });
});
