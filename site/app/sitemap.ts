import type { MetadataRoute } from "next";
import { SITE_CONTENT_LAST_MODIFIED, siteUrl } from "../lib/seo";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: siteUrl,
      lastModified: new Date(SITE_CONTENT_LAST_MODIFIED),
      changeFrequency: "weekly",
      priority: 1,
    },
  ];
}
