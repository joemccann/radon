import type { Metadata, Viewport } from "next";
import { faqStructuredData } from "./faq-content";

export const DEFAULT_SITE_URL = "https://radon.run";
export const DEMO_APP_URL = "https://demo.radon.run";
export const SITE_NAME = "Radon Terminal";
export const SITE_TITLE = "Radon Terminal | Market-Structure Reconstruction";
export const SITE_DESCRIPTION =
  "Radon scores institutional dark-pool flow from Unusual Whales, maps dealer gamma (GEX), and routes defined-risk options through Interactive Brokers.";
export const SITE_KEYWORDS = [
  "Radon Terminal",
  "market structure",
  "dark pool flow",
  "flow signal",
  "convexity",
  "regime",
  "options research",
  "institutional trading",
  "crash risk index",
  "gamma exposure",
];
export const SOCIAL_IMAGE_PATH = "/og-image.png";
export const SOCIAL_IMAGE_ALT =
  "Radon Terminal marketing card showing strategy discovery, execution discipline, and market-structure reconstruction.";
export const APPLE_ICON_PATH = "/apple-touch-icon.png";
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
      { url: "/favicon-32x32.png", sizes: "32x32", type: "image/png" },
      { url: "/favicon-16x16.png", sizes: "16x16", type: "image/png" },
      { url: "/brand/radon-app-icon.svg", type: "image/svg+xml" },
    ],
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
    featureList: [
      "Dark-pool and OTC flow scoring with per-ticker lead windows",
      "Crash Risk Index (CRI) tail-risk regime model",
      "Dealer gamma exposure (GEX) walls and magnets",
      "Volatility-Credit Gap (VCG-R) panic detection",
      "Gamma Rotation Gap (GRG) sector rotation model",
      "Four-gate execution discipline with fractional Kelly sizing",
      "Trade journal with full decision chain and lot-matched P&L",
    ],
    audience: {
      "@type": "Audience",
      audienceType: [
        "options traders",
        "flow traders",
        "gamma exposure traders",
        "day traders",
        "swing traders",
        "retail investors",
        "professional investors",
        "Interactive Brokers users",
        "Unusual Whales subscribers",
      ],
    },
    offers: {
      "@type": "Offer",
      price: "0",
      priceCurrency: "USD",
      url: DEMO_APP_URL,
      description: "Free demo instance with seeded data.",
    },
    publisher: {
      "@type": "Organization",
      name: "Radon",
      url: siteUrl,
    },
  },
  faqStructuredData(),
] as const;
