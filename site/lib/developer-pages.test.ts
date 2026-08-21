import { describe, expect, it } from "vitest";
import {
  agentPages,
  authPage,
  developersPage,
  mcpPage,
  openapiPage,
  pageToMarkdown,
  webhooksPage,
} from "./developer-pages";

describe("developer resource pages", () => {
  it("names Radon in every title and heading", () => {
    for (const page of agentPages) {
      expect(page.title).toMatch(/Radon Terminal/);
      expect(page.heading).toMatch(/Radon/);
      expect(page.description).toMatch(/Radon/);
      expect(page.slug.length).toBeGreaterThan(0);
    }
  });

  it("publishes predictable developer URLs", () => {
    expect(agentPages.map((page) => page.slug)).toEqual([
      "developers",
      "developers/openapi",
      "developers/auth",
      "developers/mcp",
      "developers/webhooks",
      "agent-instructions",
    ]);
    expect(developersPage.heading).toBe("Radon Terminal developer resources");
    expect(openapiPage.heading).toBe("Radon Terminal OpenAPI spec");
    expect(authPage.heading).toBe("Radon Terminal auth docs");
    expect(mcpPage.heading).toBe("Radon Terminal MCP server");
    expect(webhooksPage.heading).toBe("Radon Terminal webhooks");
  });

  it("keeps MCP and webhook copy honest", () => {
    expect(pageToMarkdown(mcpPage)).toContain("stdio");
    expect(pageToMarkdown(mcpPage)).toContain("kb_search");
    expect(pageToMarkdown(mcpPage)).not.toContain("https://mcp.radon.run");
    expect(pageToMarkdown(webhooksPage)).toContain(
      "does not publish a customer webhook API",
    );
    expect(pageToMarkdown(authPage)).toContain("no public API token");
  });
});
