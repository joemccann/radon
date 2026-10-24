import { describe, expect, it } from "vitest";
import {
  agentPages,
  authPage,
  developersPage,
  HOSTED_MCP_URL,
  mcpPage,
  mcpMetadata,
  openapiPage,
  openapiMetadata,
  pageToMarkdown,
  developersMetadata,
  authMetadata,
  webhooksMetadata,
  agentInstructionsMetadata,
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
    // mcp.radon.run has no DNS record or Caddy site block yet; the docs must
    // not point agents at a host that does not resolve.
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

  it("links every discovery file and named developer document", () => {
    const markdown = pageToMarkdown(developersPage);
    for (const [label, href] of [
      ["llms.txt", "https://radon.run/llms.txt"],
      ["OpenAPI 3.1 JSON", "https://radon.run/openapi.json"],
      ["Sitemap", "https://radon.run/sitemap.xml"],
      ["Agent instructions", "https://radon.run/agent-instructions"],
      ["OpenAPI docs", "https://radon.run/developers/openapi"],
      ["Auth docs", "https://radon.run/developers/auth"],
      ["MCP docs", "https://radon.run/developers/mcp"],
      ["Webhook status", "https://radon.run/developers/webhooks"],
    ]) {
      expect(markdown).toContain(`- [${label}](${href})`);
    }
  });

  it("publishes a copyable anonymous MCP first call and truthful auth boundary", () => {
    const markdown = pageToMarkdown(mcpPage);
    expect(markdown).toContain("```bash");
    expect(markdown).toContain("curl -s -X POST https://app.radon.run/mcp");
    expect(markdown).toContain("'Content-Type: application/json'");
    expect(markdown).toContain("'Accept: application/json, text/event-stream'");
    expect(markdown).toContain('"method":"tools/call"');
    expect(markdown).toContain('"name":"radon_identity"');
    expect(markdown).toContain('"arguments":{}');
    expect(markdown).toContain("user-supplied Clerk session token");
    expect(markdown).toContain("not yet agent-completable");
    expect(markdown).not.toContain("/.well-known");
  });

  it("advertises each developer page markdown alternate", () => {
    const pairs = [
      [developersPage, developersMetadata],
      [openapiPage, openapiMetadata],
      [authPage, authMetadata],
      [mcpPage, mcpMetadata],
      [webhooksPage, webhooksMetadata],
      [agentPages[5], agentInstructionsMetadata],
    ] as const;

    for (const [page, metadata] of pairs) {
      const alternates = metadata.alternates as {
        canonical?: string;
        types?: Record<string, string>;
      };
      expect(alternates.canonical).toBe(`/${page.slug}`);
      expect(alternates.types?.["text/markdown"]).toBe(`/${page.slug}.md`);
    }
  });

  it("documents the complete Robinhood data boundary", () => {
    const markdown = pageToMarkdown(agentPages[5]);
    expect(markdown).toContain(
      "Interactive Brokers > Unusual Whales > official Cboe and specialized official feeds > Robinhood > Yahoo Finance",
    );
    expect(markdown).toContain("read-only quote and chain failover");
    expect(markdown).toContain("retail-crowding overlay");
    expect(markdown).toContain("never supplies dark-pool, OTC, sweeps, GEX, or vol-surface data");
    expect(markdown).toContain("Execution stays on Interactive Brokers");
    expect(markdown).not.toMatch(/Robinhood[^.]{0,120}(routes?|routing) orders/i);
  });
});
