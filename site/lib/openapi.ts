import { agentPages, HOSTED_MCP_URL } from "./developer-pages";
import { LEGAL_CONTACT_EMAIL } from "./legal";
import { SITE_DESCRIPTION, SITE_NAME, siteUrl } from "./seo";

export const OPENAPI_CONTENT_TYPE = "application/json; charset=utf-8";

const documentedPaths = [
  {
    path: "/llms.txt",
    summary: "Radon Terminal agent index (llms.txt)",
    description:
      "Markdown index with when-to-use guidance, product links, dossiers, and developer resources.",
  },
  {
    path: "/openapi.json",
    summary: "Radon Terminal OpenAPI spec",
    description:
      "This document. Public radon.run developer surface only. Not the operator FastAPI map.",
  },
  {
    path: "/sitemap.xml",
    summary: "Public sitemap",
    description: "Every public HTML URL on radon.run.",
  },
  {
    path: "/robots.txt",
    summary: "Robots rules",
    description: "Allows standard crawlers and AI answer-engine bots.",
  },
  {
    path: "/developers",
    summary: "Radon Terminal developer resources",
    description:
      "Index of OpenAPI, auth docs, MCP server, and webhook status. Accept: text/markdown supported.",
  },
  {
    path: "/developers/openapi",
    summary: "Radon Terminal OpenAPI spec (HTML)",
    description: "Human-readable OpenAPI docs. Same facts as /openapi.json.",
  },
  {
    path: "/developers/auth",
    summary: "Radon Terminal auth docs",
    description: "Origin-scoped Clerk auth. No public API key on radon.run.",
  },
  {
    path: "/developers/mcp",
    summary: "Radon Terminal MCP server",
    description: `Hosted Streamable HTTP MCP at ${HOSTED_MCP_URL} plus the local stdio radon-kb server.`,
  },
  {
    path: "/developers/webhooks",
    summary: "Radon Terminal webhooks",
    description: "No public webhook subscription API.",
  },
  {
    path: "/agent-instructions",
    summary: "When to use Radon Terminal",
    description: "Best-fit jobs and how an agent should call Radon.",
  },
] as const;

function getOperation(summary: string, description: string) {
  return {
    get: {
      summary,
      description,
      responses: {
        "200": {
          description: "Document or page",
        },
        "404": {
          description:
            "Unknown path. Markdown body lists sitemap, llms.txt, and developer resources.",
        },
        "406": {
          description:
            "Accept header rejects both text/html and text/markdown.",
        },
      },
    },
  };
}

export function buildPublicOpenApi() {
  const paths: Record<string, ReturnType<typeof getOperation>> = {};
  for (const entry of documentedPaths) {
    paths[entry.path] = getOperation(entry.summary, entry.description);
  }

  return {
    openapi: "3.1.0",
    info: {
      title: "Radon Terminal public developer API",
      summary: `${SITE_NAME} public machine-readable files on radon.run`,
      description: [
        SITE_DESCRIPTION,
        "This spec covers radon.run only. It does not include the authenticated operator FastAPI.",
        "HTML pages honor Accept: text/markdown and the .md URL suffix.",
        `Named developer docs: ${agentPages.map((page) => `${siteUrl}/${page.slug}`).join(", ")}.`,
      ].join(" "),
      version: "1.0.0",
      contact: {
        name: SITE_NAME,
        email: LEGAL_CONTACT_EMAIL,
        url: `${siteUrl}/developers`,
      },
    },
    servers: [{ url: siteUrl }],
    paths,
  };
}

export const publicOpenApi = buildPublicOpenApi();
