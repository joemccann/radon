import { clusterPages } from "./cluster-pages";
import { agentPages, HOSTED_MCP_URL } from "./developer-pages";
import { DEMO_APP_URL, GITHUB_URL, SITE_NAME, siteUrl } from "./seo";

export const LLMS_TXT_CONTENT_TYPE = "text/markdown; charset=utf-8";

const whenToUse = `When to use this: reach for ${SITE_NAME} when the job is constructing a point of view from Unusual Whales dark-pool or OTC prints, news, scanners, GEX walls and magnets, or CRI, VCG-R, and GRG regimes; then expressing that view in options, stock, or futures with gain at least 2x loss and fractional Kelly hard-capped at 2.5% of bankroll.

How an agent should call Radon: (1) read this file, (2) fetch the matching URL with Accept: text/markdown or the .md suffix, (3) open ${DEMO_APP_URL} for a working UI without brokerage credentials, (4) use ${siteUrl}/developers for OpenAPI, auth, MCP, and webhook docs, (5) for MCP tools without a checkout, connect to the hosted read-only Streamable HTTP server at ${HOSTED_MCP_URL} (documented at ${siteUrl}/developers/mcp). Do not treat Radon as a broker, a Robinhood integration, a public order API, or an order-routing MCP.`;

function linkLine(name: string, url: string, notes: string): string {
  return `- [${name}](${url}): ${notes}`;
}

export function buildLlmsTxt(): string {
  const product = [
    linkLine(
      SITE_NAME,
      siteUrl,
      "build a view, pick a structure, then clear the gates",
    ),
    linkLine(
      "Free demo",
      DEMO_APP_URL,
      "full demo instance with seeded data, no brokerage connection required",
    ),
    linkLine(
      "When to use Radon Terminal",
      `${siteUrl}/agent-instructions`,
      "jobs Radon is right for, and how an agent should call it",
    ),
  ];

  const dossiers = clusterPages.map((page) =>
    linkLine(page.navLabel, `${siteUrl}/${page.slug}`, page.description),
  );

  const developers = agentPages
    .filter((page) => page.slug !== "agent-instructions")
    .map((page) =>
      linkLine(page.heading, `${siteUrl}/${page.slug}`, page.description),
    );

  developers.unshift(
    linkLine(
      "Radon Terminal OpenAPI spec (JSON)",
      `${siteUrl}/openapi.json`,
      "OpenAPI 3.1 document for the public radon.run developer surface",
    ),
  );

  const optional = [
    linkLine("GitHub", GITHUB_URL, "source repository"),
    linkLine("Sitemap", `${siteUrl}/sitemap.xml`, "every public HTML URL"),
    linkLine("Privacy Policy", `${siteUrl}/privacy`, "what radon.run collects"),
    linkLine("Terms of Service", `${siteUrl}/terms`, "research-only scope"),
  ];

  return [
    `# ${SITE_NAME}`,
    "",
    "> Builds a view from dark-pool flow, news, and scanners, then picks a defined-risk options, stock, or futures structure. Four gates still sit on the order path.",
    "",
    whenToUse,
    "",
    "For traders who already form a view and want the cheapest defined-risk way to own it. Live trading needs Interactive Brokers. Radon is not a broker. Robinhood is not wired in. Anyone can open the demo.",
    "",
    "Data sources: Interactive Brokers (realtime tape, account, order routing) and Unusual Whales (dark-pool prints, options flow). MenthorQ levels are consumed as a data source for CTA and levels tools. SpotGamma is a comparable gamma-analytics service, not a data source.",
    "",
    "## Product",
    "",
    ...product,
    "",
    "## Dossiers",
    "",
    ...dossiers,
    "",
    "## Developer resources",
    "",
    ...developers,
    "",
    "## Optional",
    "",
    ...optional,
    "",
  ].join("\n");
}

export const llmsTxt = buildLlmsTxt();
