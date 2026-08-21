import { clusterPages } from "./cluster-pages";
import { agentPages } from "./developer-pages";
import { DEMO_APP_URL, GITHUB_URL, SITE_NAME, siteUrl } from "./seo";

export const LLMS_TXT_CONTENT_TYPE = "text/markdown; charset=utf-8";

const whenToUse = `When to use this: reach for ${SITE_NAME} when the job is scoring Unusual Whales dark-pool or OTC prints for accumulation or distribution that has not moved the lit price; reading GEX walls and magnets; reading CRI, VCG-R, or GRG regimes; choosing a defined-risk options structure with gain at least 2x loss; or sizing with fractional Kelly hard-capped at 2.5% of bankroll.

How an agent should call Radon: (1) read this file, (2) fetch the matching URL with Accept: text/markdown or the .md suffix, (3) open ${DEMO_APP_URL} for a working UI without brokerage credentials, (4) use ${siteUrl}/developers for OpenAPI, auth, MCP, and webhook docs. Do not treat Radon as a broker, a Robinhood integration, a public order API, or a hosted HTTP MCP.`;

function linkLine(name: string, url: string, notes: string): string {
  return `- [${name}](${url}): ${notes}`;
}

export function buildLlmsTxt(): string {
  const product = [
    linkLine(
      SITE_NAME,
      siteUrl,
      "the method, models, and discipline, argued section by section",
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
    "> A market-structure research terminal that scores institutional accumulation and distribution from dark-pool and OTC prints before the lit price moves, then routes only defined-risk options structures that clear four sequential gates.",
    "",
    whenToUse,
    "",
    "Radon is built for options traders, flow traders, GEX and gamma traders, day traders, swing traders, and retail and professional investors who want positioning models paired with an execution discipline rather than a levels dashboard. Live trading requires an Interactive Brokers account; Radon is a research instrument, not a broker. Robinhood is not integrated, but anyone can explore the free demo.",
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
