import type { MetadataRoute } from "next";
import { SITE_DESCRIPTION, SITE_NAME } from "../lib/seo";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: SITE_NAME,
    short_name: "Radon",
    description: SITE_DESCRIPTION,
    start_url: "/",
    display: "standalone",
    background_color: "#0a0f14",
    theme_color: "#0a0f14",
    icons: [
      {
        src: "/brand/radon-app-icon.svg",
        sizes: "any",
        type: "image/svg+xml",
      },
    ],
  };
}
