import { VARY_ACCEPT } from "./accept";
import { LLMS_TXT_CONTENT_TYPE, llmsTxt } from "./llms-txt";
import {
  MARKDOWN_CONTENT_TYPE,
  MARKDOWN_VARY,
  markdownForSlug,
} from "./markdown-pages";
import { OPENAPI_CONTENT_TYPE, publicOpenApi } from "./openapi";

const CACHE_SHORT = "public, s-maxage=60, stale-while-revalidate=86400";
const CACHE_LONG = "public, s-maxage=300, stale-while-revalidate=86400";

export type MachineResponse = {
  status: number;
  body: string;
  headers: Record<string, string>;
};

export function markdownRouteResult(slug?: string[]): MachineResponse {
  const { status, body } = markdownForSlug(slug);
  return {
    status,
    body,
    headers: {
      "Content-Type": MARKDOWN_CONTENT_TYPE,
      Vary: MARKDOWN_VARY,
      "Cache-Control": CACHE_SHORT,
    },
  };
}

export function llmsTxtRouteResult(): MachineResponse {
  return {
    status: 200,
    body: llmsTxt,
    headers: {
      "Content-Type": LLMS_TXT_CONTENT_TYPE,
      Vary: VARY_ACCEPT,
      "Cache-Control": CACHE_LONG,
    },
  };
}

export function openApiRouteResult(): MachineResponse {
  return {
    status: 200,
    body: JSON.stringify(publicOpenApi, null, 2) + "\n",
    headers: {
      "Content-Type": OPENAPI_CONTENT_TYPE,
      Vary: VARY_ACCEPT,
      "Cache-Control": CACHE_LONG,
    },
  };
}
