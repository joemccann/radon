import { agentPages, HOSTED_MCP_URL } from "./developer-pages";
import { LEGAL_CONTACT_EMAIL } from "./legal";
import { SITE_DESCRIPTION, SITE_NAME, siteUrl } from "./seo";

export const OPENAPI_CONTENT_TYPE = "application/json; charset=utf-8";

type JsonSchema = Record<string, unknown>;

type OpenApiResponse = {
  description: string;
  content?: Record<string, { schema: JsonSchema }>;
};

type OpenApiPathItem = {
  get: {
    summary: string;
    description: string;
    parameters?: Array<{
      name: string;
      in: "header";
      required: boolean;
      description: string;
      schema: JsonSchema;
    }>;
    responses: Record<string, OpenApiResponse>;
  };
};

const textSchema = { type: "string" } as const;

const publicOpenApiSchema = {
  type: "object",
  required: ["openapi", "info", "servers", "paths"],
  properties: {
    openapi: { type: "string", const: "3.1.0" },
    info: { type: "object" },
    servers: { type: "array", items: { type: "object" } },
    paths: { type: "object", additionalProperties: true },
  },
} as const;

const fixedResources = [
  {
    path: "/llms.txt",
    summary: "Radon Terminal agent index (llms.txt)",
    description:
      "Markdown index with when-to-use guidance, product links, dossiers, and developer resources.",
    contentType: "text/markdown",
    schema: textSchema,
  },
  {
    path: "/openapi.json",
    summary: "Radon Terminal OpenAPI spec",
    description:
      "This document. Public radon.run developer surface only. Not the operator FastAPI map.",
    contentType: "application/json",
    schema: publicOpenApiSchema,
  },
  {
    path: "/sitemap.xml",
    summary: "Public sitemap",
    description: "Every public HTML URL on radon.run.",
    contentType: "application/xml",
    schema: textSchema,
  },
  {
    path: "/robots.txt",
    summary: "Robots rules",
    description: "Allows standard crawlers and AI answer-engine bots.",
    contentType: "text/plain",
    schema: textSchema,
  },
] as const;

const negotiablePages = [
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

function fixedResourceOperation(
  summary: string,
  description: string,
  contentType: string,
  schema: JsonSchema,
): OpenApiPathItem {
  return {
    get: {
      summary,
      description,
      responses: {
        "200": {
          description: "Resource returned successfully.",
          content: {
            [contentType]: { schema },
          },
        },
      },
    },
  };
}

function negotiablePageOperation(
  summary: string,
  description: string,
): OpenApiPathItem {
  return {
    get: {
      summary,
      description,
      parameters: [
        {
          name: "Accept",
          in: "header",
          required: false,
          description:
            "Choose text/html (default) or text/markdown. Quality values and wildcards are supported.",
          schema: {
            type: "string",
            examples: ["text/html", "text/markdown"],
          },
        },
      ],
      responses: {
        "200": {
          description: "Page returned as HTML or Markdown.",
          content: {
            "text/html": { schema: textSchema },
            "text/markdown": { schema: textSchema },
          },
        },
        "406": {
          description:
            "The Accept header rejects both text/html and text/markdown.",
          content: {
            "text/plain": { schema: textSchema },
          },
        },
      },
    },
  };
}

export function buildPublicOpenApi() {
  const paths: Record<string, OpenApiPathItem> = {};
  for (const entry of fixedResources) {
    paths[entry.path] = fixedResourceOperation(
      entry.summary,
      entry.description,
      entry.contentType,
      entry.schema,
    );
  }
  for (const entry of negotiablePages) {
    paths[entry.path] = negotiablePageOperation(
      entry.summary,
      entry.description,
    );
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
