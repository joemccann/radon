import { describe, expect, it } from "vitest";
import {
  NOT_FOUND_RECOVERY_LINKS,
  notFoundMarkdown,
} from "./not-found-recovery";
import { siteUrl } from "./seo";

describe("agent-friendly 404 body", () => {
  it("is short markdown that points at sitemap, llms.txt, and developer docs", () => {
    const body = notFoundMarkdown();
    expect(body.startsWith("# Page not found\n")).toBe(true);
    expect(body).toContain("## Where to look next");
    expect(body).toContain(`${siteUrl}/sitemap.xml`);
    expect(body).toContain(`${siteUrl}/llms.txt`);
    expect(body).toContain(`${siteUrl}/developers`);
    expect(NOT_FOUND_RECOVERY_LINKS).toHaveLength(5);
  });
});
