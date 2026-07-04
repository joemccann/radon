import type { MetadataRoute } from "next";
import { clusterPages } from "../lib/cluster-pages";
import { SITE_CONTENT_LAST_MODIFIED, siteUrl } from "../lib/seo";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: siteUrl,
      lastModified: new Date(SITE_CONTENT_LAST_MODIFIED),
      changeFrequency: "weekly",
      priority: 1,
    },
    ...clusterPages.map((page) => ({
      url: `${siteUrl}/${page.slug}`,
      lastModified: new Date(page.lastModified),
      changeFrequency: "weekly" as const,
      priority: 0.8,
    })),
  ];
}
