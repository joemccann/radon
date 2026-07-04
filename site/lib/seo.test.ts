import { describe, expect, it } from "vitest";
import manifest from "../app/manifest";
import robots, { AI_ANSWER_ENGINE_BOTS } from "../app/robots";
import sitemap from "../app/sitemap";
import { faqEntries } from "./faq-content";
import {
  DEFAULT_SITE_URL,
  SITE_DESCRIPTION,
  SITE_NAME,
  SITE_TITLE,
  siteMetadata,
  siteStructuredData,
  siteUrl,
  siteViewport,
} from "./seo";

describe("site SEO contract", () => {
  it("publishes canonical and social metadata", () => {
    expect(siteUrl).toBe(DEFAULT_SITE_URL);
    expect(siteMetadata.title).toBe(SITE_TITLE);
    expect(siteMetadata.description).toBe(SITE_DESCRIPTION);
    expect(siteMetadata.alternates?.canonical).toBe("/");
    expect(siteMetadata.openGraph).toMatchObject({
      type: "website",
      url: "/",
      title: SITE_TITLE,
      description: SITE_DESCRIPTION,
      siteName: SITE_NAME,
    });
    expect(siteMetadata.twitter).toMatchObject({
      card: "summary_large_image",
      title: SITE_TITLE,
      description: SITE_DESCRIPTION,
    });
    expect(siteViewport.themeColor).toBe("#0a0f14");
  });

  it("keeps the meta description entity-rich and snippet-sized", () => {
    expect(SITE_DESCRIPTION.length).toBeLessThanOrEqual(160);
    for (const entity of [
      "dark-pool",
      "Interactive Brokers",
      "Unusual Whales",
      "GEX",
      "options",
    ]) {
      expect(SITE_DESCRIPTION).toContain(entity);
    }
  });

  it("publishes structured data for website, organization, software, and FAQ", () => {
    const types = siteStructuredData.map((item) => item["@type"]);
    expect(types).toEqual([
      "WebSite",
      "Organization",
      "SoftwareApplication",
      "FAQPage",
    ]);
    expect(siteStructuredData[0]).toMatchObject({
      "@context": "https://schema.org",
      "@type": "WebSite",
      url: siteUrl,
    });
  });

  it("enriches the software application entry for answer engines", () => {
    const software = siteStructuredData.find(
      (item) => item["@type"] === "SoftwareApplication",
    ) as Record<string, unknown>;
    expect(software).toMatchObject({
      applicationCategory: "FinanceApplication",
      operatingSystem: "Web",
      isAccessibleForFree: true,
      offers: {
        "@type": "Offer",
        price: "0",
        priceCurrency: "USD",
        url: "https://demo.radon.run",
      },
    });
    const featureList = software.featureList as string[];
    expect(featureList.join(" ")).toContain("Crash Risk Index");
    expect(featureList.length).toBeGreaterThanOrEqual(6);
    const audience = software.audience as { audienceType: string[] };
    expect(audience.audienceType).toContain("options traders");
  });

  it("mirrors the FAQPage schema from the visible FAQ content", () => {
    const faq = siteStructuredData.find(
      (item) => item["@type"] === "FAQPage",
    ) as { mainEntity: Array<Record<string, unknown>> };
    expect(faq.mainEntity).toHaveLength(faqEntries.length);
    faq.mainEntity.forEach((question, index) => {
      expect(question).toEqual({
        "@type": "Question",
        name: faqEntries[index].question,
        acceptedAnswer: {
          "@type": "Answer",
          text: faqEntries[index].answer,
        },
      });
    });
  });

  it("publishes crawl routes and manifest metadata", () => {
    expect(robots()).toEqual({
      rules: [
        {
          userAgent: "*",
          allow: "/",
        },
        {
          userAgent: AI_ANSWER_ENGINE_BOTS,
          allow: "/",
        },
      ],
      sitemap: `${siteUrl}/sitemap.xml`,
      host: siteUrl,
    });
    expect(AI_ANSWER_ENGINE_BOTS).toEqual(
      expect.arrayContaining([
        "OAI-SearchBot",
        "ChatGPT-User",
        "Claude-SearchBot",
        "Claude-User",
        "PerplexityBot",
        "Perplexity-User",
        "Applebot",
      ]),
    );

    const routes = sitemap();
    expect(routes).toHaveLength(1);
    expect(routes[0]).toMatchObject({
      url: siteUrl,
      changeFrequency: "weekly",
      priority: 1,
    });
    // lastModified is stamped at build time (new Date()), not a frozen literal.
    expect(routes[0].lastModified).toBeInstanceOf(Date);
    expect(Number.isNaN(new Date(routes[0].lastModified!).getTime())).toBe(false);

    expect(manifest()).toMatchObject({
      name: SITE_NAME,
      short_name: "Radon",
      description: SITE_DESCRIPTION,
      start_url: "/",
      display: "standalone",
    });
  });
});
