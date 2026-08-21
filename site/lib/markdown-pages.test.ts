import { describe, expect, it } from "vitest";
import { clusterPages } from "./cluster-pages";
import { agentPages } from "./developer-pages";
import { legalPages } from "./legal-pages";
import {
  lookupMarkdown,
  MARKDOWN_CONTENT_TYPE,
  MARKDOWN_VARY,
  markdownForSlug,
  NOT_FOUND_RECOVERY_LINKS,
  notFoundMarkdown,
} from "./markdown-pages";
import { siteUrl } from "./seo";

describe("markdown page registry", () => {
  it("serves markdown for the homepage, dossiers, legal, status, and agent pages", () => {
    expect(lookupMarkdown("/")?.startsWith("# Radon Terminal")).toBe(true);
    expect(lookupMarkdown("/status")).toContain("Where Radon runs in public");
    for (const page of clusterPages) {
      expect(lookupMarkdown(`/${page.slug}`)).toContain(page.description);
    }
    for (const page of legalPages) {
      expect(lookupMarkdown(`/${page.slug}`)).toMatch(/^# /);
    }
    for (const page of agentPages) {
      const body = lookupMarkdown(`/${page.slug}`);
      expect(body).toContain(`# ${page.heading}`);
      expect(body).toContain("Radon");
    }
  });

  it("returns HTTP 404 markdown with sitemap and llms recovery links", () => {
    const result = markdownForSlug(["some-path-that-does-not-exist"]);
    expect(result.status).toBe(404);
    expect(result.body).toBe(notFoundMarkdown());
    expect(result.body).toContain("# Page not found");
    expect(result.body).toContain(`${siteUrl}/sitemap.xml`);
    expect(result.body).toContain(`${siteUrl}/llms.txt`);
    expect(result.body).toContain(`${siteUrl}/developers`);
    expect(NOT_FOUND_RECOVERY_LINKS.map((link) => link.href)).toEqual(
      expect.arrayContaining([
        `${siteUrl}/sitemap.xml`,
        `${siteUrl}/llms.txt`,
        `${siteUrl}/developers`,
      ]),
    );
  });

  it("publishes the acceptmarkdown media type and Vary contract", () => {
    expect(MARKDOWN_CONTENT_TYPE).toBe("text/markdown; charset=utf-8");
    expect(MARKDOWN_VARY).toBe("Accept, Accept-Encoding");
  });
});
