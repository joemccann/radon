import type { MetadataRoute } from "next";
import { siteUrl } from "../lib/seo";

// AI answer-engine crawlers (search-index + user-fetch bots) explicitly
// allowed so radon.run stays citable in ChatGPT, Claude, Perplexity,
// Meta AI, Alexa/Rufus, Siri, DuckAssist, and Le Chat answers. /_next/ is
// disallowed in both groups: GSC was spending crawl budget on hashed
// chunks (15 of 22 not-indexed URLs on 2026-08-20). HTML is SSR'd.
export const AI_ANSWER_ENGINE_BOTS = [
  "OAI-SearchBot",
  "ChatGPT-User",
  "Claude-SearchBot",
  "Claude-User",
  "PerplexityBot",
  "Perplexity-User",
  "meta-webindexer",
  "meta-externalfetcher",
  "Amzn-SearchBot",
  "Amzn-User",
  "Applebot",
  "DuckAssistBot",
  "MistralAI-User",
];

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: "/_next/",
      },
      {
        userAgent: AI_ANSWER_ENGINE_BOTS,
        allow: "/",
        disallow: "/_next/",
      },
    ],
    sitemap: `${siteUrl}/sitemap.xml`,
    host: siteUrl,
  };
}
