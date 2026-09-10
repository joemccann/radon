import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { agentPages } from "./developer-pages";
import { llmsTxt } from "./llms-txt";
import { siteUrl } from "./seo";

describe("llms.txt agent index", () => {
  it("follows the llmstxt.org shape with when-to-use guidance", () => {
    expect(llmsTxt.startsWith("# Radon Terminal\n")).toBe(true);
    expect(llmsTxt).toMatch(/^# Radon Terminal\n\n> /);
    expect(llmsTxt).toContain("When to use this:");
    expect(llmsTxt).toContain("How an agent should call Radon:");
    expect(llmsTxt).toContain("dark-pool");
    expect(llmsTxt).toContain("fractional Kelly");
    expect(llmsTxt).toContain("Accept: text/markdown");
    expect(llmsTxt).toContain(`${siteUrl}/agent-instructions`);
  });

  it("lists developer resources by name at predictable URLs", () => {
    expect(llmsTxt).toContain("## Developer resources");
    expect(llmsTxt).toContain(`${siteUrl}/openapi.json`);
    for (const page of agentPages) {
      expect(llmsTxt).toContain(`${siteUrl}/${page.slug}`);
      expect(llmsTxt).toContain(page.heading);
    }
    expect(llmsTxt).toContain("Radon Terminal OpenAPI spec");
    expect(llmsTxt).toContain("Radon Terminal auth docs");
    expect(llmsTxt).toContain("Radon Terminal MCP server");
    expect(llmsTxt).toContain("Radon Terminal webhooks");
    expect(llmsTxt).toContain(`${siteUrl}/developers/recipes`);
    expect(llmsTxt).toContain("Radon Terminal developer recipes");
  });

  it("keeps H2 sections as file lists", () => {
    const sections = llmsTxt.split("\n## ").slice(1);
    expect(sections.length).toBeGreaterThanOrEqual(3);
    for (const section of sections) {
      const items = section
        .split("\n")
        .filter((line) => line.startsWith("- "));
      expect(items.length).toBeGreaterThan(0);
      for (const item of items) {
        expect(item).toMatch(/^- \[[^\]]+\]\(https:\/\/[^)]+\): /);
      }
    }
  });

  it("matches the published public file when one is present", () => {
    const published = path.join(process.cwd(), "site/public/llms.txt");
    try {
      const file = readFileSync(published, "utf8");
      expect(file).toBe(llmsTxt);
    } catch {
      // Served from app/llms.txt/route.ts; the module is the source of truth.
    }
  });
});
