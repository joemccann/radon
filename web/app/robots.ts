import type { MetadataRoute } from "next";
import { PUBLIC_SHARE_API_ROUTES } from "@/lib/publicShareRoutes";

// The authenticated terminal (app/demo.radon.run) must never be indexed —
// all marketing/SEO value lives on radon.run (site/). No sitemap on purpose.
// Paired with the X-Robots-Tag: noindex header in next.config.mjs, which also
// covers URLs Google already indexed (robots.txt alone doesn't de-index).
//
// The read-only share-card routes are carved out with Allow: link-preview bots
// (Twitterbot, Slackbot) honor robots.txt, and a blanket Disallow: / would
// stop shared tweet cards from unfurling. The card GENERATOR POSTs are
// deliberately NOT carved out (a bot never POSTs, and they now require a Clerk
// session). Longest-match precedence means the
// Allow rules win over Disallow: / for those paths. The global noindex header
// still keeps them out of search results — noindex governs indexing, not
// preview fetching.
//
// Googlebot is a separate Allow: / group on purpose. GSC 2026-08-20 listed
// demo.radon.run as "Indexed, though blocked by robots.txt": Disallow: /
// stopped recrawl, so Google never saw the noindex header and could not
// drop the URL. Googlebot must be able to fetch the page to honor noindex.
export const GOOGLE_INDEXING_BOTS = [
  "Googlebot",
  "Googlebot-Image",
  "Google-InspectionTool",
] as const;

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: [...PUBLIC_SHARE_API_ROUTES],
        disallow: "/",
      },
      {
        userAgent: [...GOOGLE_INDEXING_BOTS],
        allow: "/",
      },
    ],
  };
}
