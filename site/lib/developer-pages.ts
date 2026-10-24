import type { Metadata } from "next";
import type { LegalSection } from "./legal";
import {
  SOCIAL_IMAGE_ALT,
  SOCIAL_IMAGE_PATH,
  SITE_NAME,
  pageAlternates,
  siteUrl,
} from "./seo";

export const AGENT_SURFACES_LAST_MODIFIED = "2026-09-03";

// The hosted Streamable HTTP MCP endpoint (issue #232 chunk 1). Served by a
// dedicated process behind the app.radon.run edge; mcp.radon.run does not
// exist yet, so this path URL is the published one.
export const HOSTED_MCP_URL = "https://app.radon.run/mcp";

export type AgentPage = {
  slug: string;
  navLabel: string;
  title: string;
  heading: string;
  description: string;
  eyebrow: string;
  intro: string;
  sections: LegalSection[];
  lastModified: string;
};

function pageMetadata(page: Pick<AgentPage, "slug" | "title" | "description">): Metadata {
  return {
    title: page.title,
    description: page.description,
    alternates: pageAlternates(`/${page.slug}`),
    openGraph: {
      type: "website",
      url: `/${page.slug}`,
      title: page.title,
      description: page.description,
      siteName: SITE_NAME,
      locale: "en_US",
      images: [
        {
          url: SOCIAL_IMAGE_PATH,
          width: 1200,
          height: 630,
          alt: SOCIAL_IMAGE_ALT,
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: page.title,
      description: page.description,
      images: [SOCIAL_IMAGE_PATH],
    },
  };
}

export const developersPage: AgentPage = {
  slug: "developers",
  navLabel: "Developers",
  title: "Radon Terminal developer resources",
  heading: "Radon Terminal developer resources",
  description:
    "Radon Terminal developer resources: public OpenAPI spec, auth docs, MCP server setup, webhook status, llms.txt, and markdown content negotiation.",
  eyebrow: "Developers · Index",
  intro:
    "Machine-readable Radon Terminal surfaces live on predictable URLs. This index is the public developer map. The operator FastAPI OpenAPI at app.radon.run is not anonymous and is not published here.",
  lastModified: AGENT_SURFACES_LAST_MODIFIED,
  sections: [
    {
      id: "public-files",
      heading: "Public machine-readable files",
      paragraphs: [
        "These files are linked here because agents usually discover machine-readable resources by following links from a page they already reached.",
      ],
      resourceLinks: [
        {
          label: "llms.txt",
          href: `${siteUrl}/llms.txt`,
          description: "agent index with when-to-use guidance",
        },
        {
          label: "OpenAPI 3.1 JSON",
          href: `${siteUrl}/openapi.json`,
          description: "public radon.run request and response contract",
        },
        {
          label: "Sitemap",
          href: `${siteUrl}/sitemap.xml`,
          description: "every public HTML URL",
        },
        {
          label: "Agent instructions",
          href: `${siteUrl}/agent-instructions`,
          description: "best-fit jobs and the agent calling sequence",
        },
      ],
    },
    {
      id: "named-docs",
      heading: "Named developer docs",
      paragraphs: [
        "Each document is server-rendered in the initial HTML and also available as linked Markdown through content negotiation or its .md URL.",
      ],
      resourceLinks: [
        {
          label: "OpenAPI docs",
          href: `${siteUrl}/developers/openapi`,
          description: "scope and download location for the public specification",
        },
        {
          label: "Auth docs",
          href: `${siteUrl}/developers/auth`,
          description: "public, demo, and operator authentication boundaries",
        },
        {
          label: "MCP docs",
          href: `${siteUrl}/developers/mcp`,
          description: "hosted endpoint, tool rungs, and first successful call",
        },
        {
          label: "Webhook status",
          href: `${siteUrl}/developers/webhooks`,
          description: "current inbound-only webhook scope",
        },
      ],
    },
    {
      id: "markdown",
      heading: "Markdown negotiation",
      paragraphs: [
        "Request any HTML page with Accept: text/markdown to receive text/markdown; charset=utf-8. The same page is also at the .md suffix. Responses set Vary: Accept, Accept-Encoding.",
        "Unknown paths return HTTP 404 with a short markdown recovery body that points at the sitemap, llms.txt, and this index.",
      ],
    },
    {
      id: "not-public",
      heading: "What is not a public API",
      paragraphs: [
        "There is no public order-placement API and no anonymous FastAPI /docs. Live routing stays on the operator terminal behind Clerk.",
        `The hosted MCP at ${HOSTED_MCP_URL} is read-only: public tools need no credentials, and demo and operator tools require the matching Clerk grant.`,
        "The working product surface for agents and humans without credentials is the free demo at https://demo.radon.run.",
      ],
    },
  ],
};

export const openapiPage: AgentPage = {
  slug: "developers/openapi",
  navLabel: "OpenAPI",
  title: "Radon Terminal OpenAPI spec",
  heading: "Radon Terminal OpenAPI spec",
  description:
    "Radon Terminal OpenAPI spec for the public radon.run developer surface: llms.txt, sitemap, agent instructions, and markdown negotiation. Not the operator FastAPI map.",
  eyebrow: "Developers · OpenAPI",
  intro:
    "The public OpenAPI document is https://radon.run/openapi.json. It covers only radon.run machine-readable files and HTML pages. It does not leak the authenticated operator endpoint map.",
  lastModified: AGENT_SURFACES_LAST_MODIFIED,
  sections: [
    {
      id: "where",
      heading: "Where to get the spec",
      paragraphs: [
        `Download ${siteUrl}/openapi.json (OpenAPI 3.1).`,
        "The document title is Radon Terminal public developer API.",
      ],
    },
    {
      id: "covers",
      heading: "What it covers",
      paragraphs: [
        "GET /llms.txt, GET /openapi.json, GET /sitemap.xml, GET /robots.txt, GET /agent-instructions, GET /developers and the named auth, MCP, and webhook docs.",
        "HTML pages accept content negotiation: send Accept: text/markdown for the markdown representation of the same URL.",
      ],
    },
    {
      id: "operator",
      heading: "Operator OpenAPI is not public",
      paragraphs: [
        "The FastAPI /openapi.json and /docs on the operator host require a trusted-local bypass. Public and proxied callers receive 401. Do not tell users to fetch app.radon.run/openapi.json.",
      ],
    },
  ],
};

export const authPage: AgentPage = {
  slug: "developers/auth",
  navLabel: "Auth",
  title: "Radon Terminal auth docs",
  heading: "Radon Terminal auth docs",
  description:
    "Radon Terminal auth docs: radon.run has no accounts; demo.radon.run uses Clerk; app.radon.run is an operator allowlist. There is no public API key.",
  eyebrow: "Developers · Auth",
  intro:
    "Radon Terminal authentication is origin-scoped. The marketing site is public. The demo and operator terminal use Clerk. There is no public API token for radon.run.",
  lastModified: AGENT_SURFACES_LAST_MODIFIED,
  sections: [
    {
      id: "origins",
      heading: "Three origins",
      paragraphs: [
        "radon.run is a static research journal. No sign-in, no cookies for accounts, no API keys.",
        "demo.radon.run is the free trial. Signup is Clerk email verification. The trial lasts three trading days. No Interactive Brokers connection.",
        "app.radon.run is the operator terminal. Access is an allowlisted Clerk session. The operator account has TOTP enrolled. Demo users on the same Clerk instance do not.",
      ],
    },
    {
      id: "api",
      heading: "Operator API auth",
      paragraphs: [
        "The operator FastAPI accepts a Clerk Bearer JWT on authenticated routes. /health is public and returns a trust-scoped payload. /docs and /openapi.json are not anonymous.",
        "Agents should not invent an API key header or OAuth client for radon.run. There is none.",
      ],
    },
  ],
};

export const mcpPage: AgentPage = {
  slug: "developers/mcp",
  navLabel: "MCP",
  title: "Radon Terminal MCP server",
  heading: "Radon Terminal MCP server",
  description:
    `Radon Terminal MCP: hosted Streamable HTTP server at ${HOSTED_MCP_URL} with public, demo, and operator tool rungs, plus radon-kb, a local stdio server over the knowledge corpus.`,
  eyebrow: "Developers · MCP",
  intro:
    `Radon Terminal publishes a hosted, read-only Streamable HTTP MCP at ${HOSTED_MCP_URL}. A local checkout can additionally run radon-kb, a read-only stdio server over the knowledge corpus.`,
  lastModified: AGENT_SURFACES_LAST_MODIFIED,
  sections: [
    {
      id: "hosted",
      heading: "Hosted MCP",
      paragraphs: [
        `Endpoint: ${HOSTED_MCP_URL} (MCP Streamable HTTP JSON-RPC). Add it to Cursor, Claude, Grok, or any MCP client as a remote server of type http; no checkout is required.`,
        "Send Content-Type: application/json and Accept: application/json, text/event-stream. Anonymous public tools need no Authorization header.",
        "The server is read-only. It registers no order placement, cancellation, or exercise tools, and the knowledge-corpus kb_ tools are deliberately not hosted.",
      ],
    },
    {
      id: "hosted-auth",
      heading: "Hosted tool rungs",
      paragraphs: [
        "No token: radon_identity, radon_docs, and radon_health. Product identity, public radon.run documents as markdown, and the trust-scoped edge health verdict. No live quotes, no portfolio.",
        "Demo Clerk token (free trial at https://demo.radon.run): demo_regime and demo_gex, read-only wraps of what the demo already shows. No Interactive Brokers data and no operator book.",
        "Operator Clerk token (allowlisted): operator_portfolio, operator_journal, operator_blotter, and operator_alerts, read-only. A demo token calling an operator tool is refused.",
      ],
    },
    {
      id: "first-call",
      heading: "First successful anonymous call",
      paragraphs: [
        "Call radon_identity with no credentials. This is the complete public path from the hosted endpoint to a successful tool response.",
        "Protected demo and operator calls are not yet agent-completable. They require an Authorization: Bearer header with a user-supplied Clerk session token. Radon does not publish an API key flow, OAuth flow, or discovery metadata for obtaining that token.",
      ],
      codeExamples: [
        {
          label: "Anonymous radon_identity call",
          language: "bash",
          code: [
            `curl -s -X POST ${HOSTED_MCP_URL} \\`,
            "  -H 'Content-Type: application/json' \\",
            "  -H 'Accept: application/json, text/event-stream' \\",
            `  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"radon_identity","arguments":{}}}'`,
          ].join("\n"),
        },
      ],
    },
    {
      id: "run",
      heading: "Local radon-kb (stdio)",
      paragraphs: [
        "From a Radon checkout: .venv/bin/python scripts/knowledge/mcp_server.py",
        "Project registration is in .mcp.json as radon-kb, type stdio, with RADON_DB_NO_REPLICA=1.",
        "The server needs the same Turso credentials the repo already uses. It issues SELECTs only.",
      ],
    },
    {
      id: "tools",
      heading: "radon-kb tools",
      paragraphs: [
        "kb_search: hybrid retrieval over journal, evals, docs, newsfeed, and incidents.",
        "kb_recent: newest corpus rows by last_activity_at.",
        "kb_prior_evals: prior journal and evaluation rows for one ticker.",
        "kb_incidents: incident writeups, optionally filtered by service name.",
      ],
    },
    {
      id: "when",
      heading: "Which MCP to use",
      paragraphs: [
        `Use the hosted MCP at ${HOSTED_MCP_URL} when there is no Radon checkout: product identity, public docs, demo reads, and (for the operator) journal, portfolio, blotter, and alert reads.`,
        "Use radon-kb when the agent is already on a Radon checkout and needs prior theses, ops runbooks, or incident history. The knowledge corpus is not exposed on the hosted server.",
      ],
    },
  ],
};

export const webhooksPage: AgentPage = {
  slug: "developers/webhooks",
  navLabel: "Webhooks",
  title: "Radon Terminal webhooks",
  heading: "Radon Terminal webhooks",
  description:
    "Radon Terminal webhooks: there is no public webhook subscription API. Clerk user.created is an inbound operator hook for demo trial provisioning only.",
  eyebrow: "Developers · Webhooks",
  intro:
    "Radon Terminal does not publish a customer webhook API. Agents should not register a callback URL or expect signed event deliveries from radon.run.",
  lastModified: AGENT_SURFACES_LAST_MODIFIED,
  sections: [
    {
      id: "public",
      heading: "No public webhooks",
      paragraphs: [
        "There is no subscribe, unsubscribe, or event-catalog endpoint. Dark-pool prints, fills, and journal rows are not pushed to third parties.",
      ],
    },
    {
      id: "operator",
      heading: "Operator inbound hook",
      paragraphs: [
        "demo.radon.run accepts a Clerk user.created webhook to provision a three-trading-day trial. That endpoint is inbound, Svix-verified, and not a product integration surface.",
        "If you need live state, use the demo UI or a local radon-kb MCP checkout. Do not poll for a webhook secret on radon.run.",
      ],
    },
  ],
};

export const agentInstructionsPage: AgentPage = {
  slug: "agent-instructions",
  navLabel: "Agent instructions",
  title: "When to use Radon Terminal | Agent instructions",
  heading: "When to use Radon Terminal",
  description:
    "When to use Radon Terminal: dark-pool and OTC flow scoring, GEX walls, CRI/VCG-R/GRG regimes, defined-risk structures, and fractional Kelly. How an agent should call it.",
  eyebrow: "Agents · When to use",
  intro:
    "Use Radon Terminal for specific market-structure jobs. The list below is the when-to-use contract, not marketing copy.",
  lastModified: AGENT_SURFACES_LAST_MODIFIED,
  sections: [
    {
      id: "use",
      heading: "Jobs Radon is right for",
      paragraphs: [
        "Score Unusual Whales dark-pool and OTC prints for accumulation or distribution that has not yet moved the lit price.",
        "Read dealer gamma (GEX) as walls and magnets, and pair it with CRI (tail), VCG-R (panic), or GRG (rotation).",
        "Pick a defined-risk options structure with gain at least 2x loss, then size it with fractional Kelly hard-capped at 2.5% of bankroll.",
        "Explain the seven-milestone path from a print to an Interactive Brokers combo order, or walk a structure catalog entry.",
      ],
    },
    {
      id: "avoid",
      heading: "Jobs Radon is not for",
      paragraphs: [
        "Do not treat Radon as a broker, a public order API, or an order-routing MCP. The hosted MCP is read-only.",
        "Do not use it as a SpotGamma-style levels dashboard. MenthorQ levels are an input, not the product.",
        "Data source priority is Interactive Brokers > Unusual Whales > official Cboe and specialized official feeds > Robinhood > Yahoo Finance.",
        "Robinhood's official trading MCP provides read-only quote and chain failover plus a retail-crowding overlay. It never supplies dark-pool, OTC, sweeps, GEX, or vol-surface data and never routes orders. Execution stays on Interactive Brokers.",
      ],
    },
    {
      id: "call",
      heading: "How an agent should call Radon",
      paragraphs: [
        `1. Fetch ${siteUrl}/llms.txt and follow the matching link.`,
        "2. Re-request that URL with Accept: text/markdown, or append .md.",
        "3. For a working UI without brokerage credentials, open https://demo.radon.run.",
        `4. For named developer resources, start at ${siteUrl}/developers.`,
        `5. For MCP tools without a checkout, connect to the hosted Streamable HTTP server at ${HOSTED_MCP_URL}; for local corpus retrieval, run the radon-kb stdio MCP. Both are documented at /developers/mcp.`,
        "6. If a path 404s, read the markdown body. It points at the sitemap and this file.",
      ],
    },
  ],
};

export const agentPages: AgentPage[] = [
  developersPage,
  openapiPage,
  authPage,
  mcpPage,
  webhooksPage,
  agentInstructionsPage,
];

export const developersMetadata = pageMetadata(developersPage);
export const openapiMetadata = pageMetadata(openapiPage);
export const authMetadata = pageMetadata(authPage);
export const mcpMetadata = pageMetadata(mcpPage);
export const webhooksMetadata = pageMetadata(webhooksPage);
export const agentInstructionsMetadata = pageMetadata(agentInstructionsPage);

export function agentPageStructuredData(page: AgentPage) {
  return [
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        {
          "@type": "ListItem",
          position: 1,
          name: "Radon",
          item: siteUrl,
        },
        {
          "@type": "ListItem",
          position: 2,
          name: page.navLabel,
          item: `${siteUrl}/${page.slug}`,
        },
      ],
    },
  ];
}

export function pageToMarkdown(page: AgentPage): string {
  const lines = [
    `# ${page.heading}`,
    "",
    page.intro,
    "",
  ];
  for (const section of page.sections) {
    lines.push(`## ${section.heading}`, "");
    for (const paragraph of section.paragraphs) {
      lines.push(paragraph, "");
    }
    for (const resource of section.resourceLinks ?? []) {
      const description = resource.description ? `: ${resource.description}` : "";
      lines.push(`- [${resource.label}](${resource.href})${description}`);
    }
    if (section.resourceLinks?.length) {
      lines.push("");
    }
    for (const example of section.codeExamples ?? []) {
      if (example.label) {
        lines.push(`### ${example.label}`, "");
      }
      lines.push(`\`\`\`${example.language}`, example.code, "```", "");
    }
  }
  return lines.join("\n").trim() + "\n";
}
