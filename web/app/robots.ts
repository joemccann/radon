import type { MetadataRoute } from "next";
import { PUBLIC_SHARE_API_ROUTES } from "@/lib/publicShareRoutes";

// The authenticated terminal (app/demo/beta.radon.run) must never be indexed —
// all marketing/SEO value lives on radon.run (site/). No sitemap on purpose.
// Paired with the X-Robots-Tag: noindex header in next.config.mjs, which also
// covers URLs Google already indexed (robots.txt alone doesn't de-index).
//
// The share-card routes are carved out with Allow: link-preview bots
// (Twitterbot, Slackbot) honor robots.txt, and a blanket Disallow: / would
// stop shared tweet cards from unfurling. Longest-match precedence means the
// Allow rules win over Disallow: / for those paths. The global noindex header
// still keeps them out of search results — noindex governs indexing, not
// preview fetching.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: [...PUBLIC_SHARE_API_ROUTES],
      disallow: "/",
    },
  };
}
