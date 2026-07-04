import { describe, expect, it } from "vitest";
import manifest from "../app/manifest";
import robots, { AI_ANSWER_ENGINE_BOTS } from "../app/robots";
import sitemap from "../app/sitemap";
import { clusterPages } from "./cluster-pages";
import {
  DEFAULT_SITE_URL,
  SITE_CONTENT_LAST_MODIFIED,
  SITE_DESCRIPTION,
  SITE_NAME,
  SITE_TITLE,
  X_PROFILE_URL,
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

  it("spends the title keyword slot on persona queries, not internal jargon", () => {
    expect(SITE_TITLE.startsWith("Radon Terminal | ")).toBe(true);
    expect(SITE_TITLE.length).toBeLessThanOrEqual(60);
    for (const entity of ["Dark Pool Flow", "GEX", "Options"]) {
      expect(SITE_TITLE).toContain(entity);
    }
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

  it("publishes only site-scoped structured data from the root layout", () => {
    // FAQPage is homepage content, not site-wide: it is emitted by app/page.tsx
    // (see faq-content.test.ts for the schema mirror), never by the layout,
    // so cluster pages carry exactly one FAQPage each.
    const types = siteStructuredData.map((item) => item["@type"]);
    expect(types).toEqual(["WebSite", "Organization", "SoftwareApplication"]);
    expect(siteStructuredData[0]).toMatchObject({
      "@context": "https://schema.org",
      "@type": "WebSite",
      url: siteUrl,
    });
  });

  it("connects the operator identity across Organization sameAs and founder", () => {
    const organization = siteStructuredData.find(
      (item) => item["@type"] === "Organization",
    ) as Record<string, unknown>;
    expect(organization.sameAs).toEqual(
      expect.arrayContaining([
        "https://github.com/joemccann/radon",
        X_PROFILE_URL,
      ]),
    );
    expect(organization.founder).toMatchObject({
      "@type": "Person",
      name: "Joe McCann",
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
    expect(routes).toHaveLength(7);
    expect(routes).toHaveLength(1 + clusterPages.length);
    expect(routes[0]).toMatchObject({
      url: siteUrl,
      changeFrequency: "weekly",
      priority: 1,
    });
    // lastmod must be a frozen content date, never stamped at request time:
    // Google ignores lastmod when it always equals "now".
    expect(routes[0].lastModified).toEqual(new Date(SITE_CONTENT_LAST_MODIFIED));
    clusterPages.forEach((page, index) => {
      expect(routes[index + 1]).toMatchObject({
        url: `${siteUrl}/${page.slug}`,
        changeFrequency: "weekly",
        priority: 0.8,
      });
      expect(routes[index + 1].lastModified).toEqual(
        new Date(page.lastModified),
      );
    });
    for (const route of routes) {
      const lastModified = new Date(route.lastModified!);
      expect(Number.isNaN(lastModified.getTime())).toBe(false);
      expect(lastModified.getTime()).toBeLessThanOrEqual(Date.now());
    }

    expect(manifest()).toMatchObject({
      name: SITE_NAME,
      short_name: "Radon",
      description: SITE_DESCRIPTION,
      start_url: "/",
      display: "standalone",
    });
  });
});
