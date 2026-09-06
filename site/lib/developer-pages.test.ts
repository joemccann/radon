import { describe, expect, it } from "vitest";
import {
  agentPages,
  authPage,
  developersPage,
  HOSTED_MCP_URL,
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
      "developers/recipes",
      "developers/openapi",
      "developers/auth",
      "developers/mcp",
      "developers/webhooks",
      "agent-instructions",
    ]);
    expect(developersPage.heading).toBe("Radon Terminal developer resources");
    expect(developersPage.sections.map((section) => section.id)).toContain(
      "agent-prompt-payload",
    );
    expect(pageToMarkdown(developersPage)).toContain(
      "Field order is stable: title, When to use, Hard nos, How to call, Parameters / constraints, Definition of done for the agent.",
    );
    expect(openapiPage.heading).toBe("Radon Terminal OpenAPI spec");
    expect(authPage.heading).toBe("Radon Terminal auth docs");
    expect(mcpPage.heading).toBe("Radon Terminal MCP server");
    expect(webhooksPage.heading).toBe("Radon Terminal webhooks");
    expect(agentPages.find((page) => page.slug === "developers/recipes")?.heading).toBe(
      "Radon Terminal developer recipes",
    );
  });

  it("keeps MCP and webhook copy honest", () => {
    expect(pageToMarkdown(mcpPage)).toContain("stdio");
    expect(pageToMarkdown(mcpPage)).toContain("kb_search");
    expect(pageToMarkdown(webhooksPage)).toContain(
      "does not publish a customer webhook API",
    );
    expect(pageToMarkdown(authPage)).toContain("no public API token");
  });

  it("publishes the hosted MCP at the URL that actually serves it", () => {
    expect(HOSTED_MCP_URL).toBe("https://app.radon.run/mcp");
    const markdown = pageToMarkdown(mcpPage);
    expect(markdown).toContain(HOSTED_MCP_URL);
    expect(markdown).toContain("Streamable HTTP");
    expect(markdown).toContain("read-only");
    // Dedicated host exists (DNS + Caddy) but is not the published consumer
    // URL until live TLS is verified; keep agents on app.radon.run/mcp.
    expect(markdown).not.toContain("https://mcp.radon.run");
    // The hosted rungs are named, and the corpus stays local-only.
    expect(markdown).toContain("radon_identity");
    expect(markdown).toContain("demo_regime");
    expect(markdown).toContain("operator_portfolio");
    expect(markdown).toContain(
      "The knowledge corpus is not exposed on the hosted server.",
    );
  });

  it("keeps the hosted MCP out of the not-a-broker guardrails", () => {
    const developers = pageToMarkdown(developersPage);
    expect(developers).toContain(HOSTED_MCP_URL);
    expect(developers).toContain("no public order-placement API");
  });
});
