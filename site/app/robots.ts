import type { MetadataRoute } from "next";
import { siteUrl } from "../lib/seo";

// AI answer-engine crawlers (search-index + user-fetch bots) explicitly
// allowed so radon.run stays citable in ChatGPT, Claude, Perplexity,
// Meta AI, Alexa/Rufus, Siri, DuckAssist, and Le Chat answers. The
// explicit group also shields them from any future wildcard disallows.
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
      },
      {
        userAgent: AI_ANSWER_ENGINE_BOTS,
        allow: "/",
      },
    ],
    sitemap: `${siteUrl}/sitemap.xml`,
    host: siteUrl,
  };
}
