import type { Metadata, Viewport } from "next";

export const DEFAULT_SITE_URL = "https://radon.run";
export const SITE_NAME = "Radon Terminal";
export const SITE_TITLE = "Radon Terminal | Strategies, Execution, and Market Structure";
export const SITE_DESCRIPTION =
  "Radon Terminal is an institutional-grade instrument for strategy discovery, execution discipline, and market-structure reconstruction.";
export const SITE_KEYWORDS = [
  "Radon Terminal",
  "market structure",
  "options trading",
  "dark pool flow",
  "crash risk index",
  "volatility trading",
  "portfolio analytics",
  "trading terminal",
];
export const SOCIAL_IMAGE_PATH = "/opengraph-image";
export const SOCIAL_IMAGE_ALT =
  "Radon Terminal marketing card showing strategy discovery, execution discipline, and market-structure reconstruction.";
export const APPLE_ICON_PATH = "/apple-icon";
export const GITHUB_URL = "https://github.com/joemccann/radon";

function normalizeSiteUrl(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

export const siteUrl = normalizeSiteUrl(
  process.env.NEXT_PUBLIC_SITE_URL?.trim() || DEFAULT_SITE_URL,
);

export const metadataBase = new URL(siteUrl);

export const siteMetadata: Metadata = {
  metadataBase,
  title: SITE_TITLE,
  description: SITE_DESCRIPTION,
  applicationName: SITE_NAME,
  authors: [{ name: "Joe McCann", url: GITHUB_URL }],
  creator: "Joe McCann",
  publisher: SITE_NAME,
  referrer: "origin-when-cross-origin",
  keywords: SITE_KEYWORDS,
  category: "finance",
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    locale: "en_US",
    url: "/",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
    siteName: SITE_NAME,
    images: [
      {
        url: SOCIAL_IMAGE_PATH,
        width: 1200,
        height: 630,
        alt: SOCIAL_IMAGE_ALT,
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    creator: "@joemccann",
    site: "@joemccann",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
    images: [SOCIAL_IMAGE_PATH],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
      "max-video-preview": -1,
    },
  },
  icons: {
    icon: [
      { url: "/favicon.ico" },
      { url: "/brand/radon-app-icon.svg", type: "image/svg+xml" },
    ],
    shortcut: "/favicon.ico",
    apple: [{ url: APPLE_ICON_PATH, sizes: "180x180", type: "image/png" }],
  },
  manifest: "/manifest.webmanifest",
};

export const siteViewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0a0f14",
  colorScheme: "dark",
};

export const siteStructuredData = [
  {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: SITE_NAME,
    url: siteUrl,
    description: SITE_DESCRIPTION,
    inLanguage: "en-US",
  },
  {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: "Radon",
    url: siteUrl,
    logo: `${siteUrl}/brand/radon-app-icon.svg`,
    sameAs: [GITHUB_URL],
  },
  {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: SITE_NAME,
    applicationCategory: "FinanceApplication",
    operatingSystem: "Web",
    url: siteUrl,
    description: SITE_DESCRIPTION,
    isAccessibleForFree: true,
    publisher: {
      "@type": "Organization",
      name: "Radon",
      url: siteUrl,
    },
  },
] as const;
