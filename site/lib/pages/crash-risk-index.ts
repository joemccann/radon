// Content module for /crash-risk-index: typed copy arrays, FAQ entries,
// per-page constants, metadata, and JSON-LD builders. Single source of truth
// so the schema always mirrors the visible copy (same pattern as
// lib/faq-content.ts). The metadata object matches the clusterPageMetadata
// contract shape so the integrator can swap it for the shared lib/seo.ts
// helper without changing the page.
//
// Method source of truth: docs/strategies.md (Strategy 6) and
// scripts/cri_scan.py compute_cri / web/lib/criCalc.ts. Four components,
// each scored 0 to 25 on published linear bands; the scores sum to 0 to 100.

import type { Metadata } from "next";
import type { FaqEntry } from "../faq-content";
import {
  SITE_NAME,
  SOCIAL_IMAGE_ALT,
  SOCIAL_IMAGE_PATH,
  siteUrl,
} from "../seo";

export const CRASH_RISK_INDEX_SLUG = "crash-risk-index";
export const CRASH_RISK_INDEX_NAV_LABEL = "Crash Risk Index";

export const CRASH_RISK_INDEX_TITLE =
  "CRI Crash Risk Index: VIX, VVIX, COR1M | Radon Terminal";
export const CRASH_RISK_INDEX_DESCRIPTION =
  "CRI is Radon's Crash Risk Index: VIX, VVIX, COR1M implied correlation, and SPX trend, each scored 0 to 25 and summed into a published 0 to 100 crash regime read.";

// The single h1 on the page; the TechArticle headline must mirror it exactly.
export const CRASH_RISK_INDEX_H1 =
  "Forced selling is mechanical. The Crash Risk Index reads the regime that triggers it.";

// Frozen per-page dates (never request-time; same rationale as
// SITE_CONTENT_LAST_MODIFIED in lib/seo.ts). Bump dateModified when the copy
// meaningfully changes.
export const CRASH_RISK_INDEX_DATE_PUBLISHED = "2026-07-03";
export const CRASH_RISK_INDEX_DATE_MODIFIED = "2026-08-23";

export const crashRiskIndexUrl = `${siteUrl}/${CRASH_RISK_INDEX_SLUG}`;

// The four published components (docs/strategies.md, Strategy 6; scored in
// scripts/cri_scan.py compute_cri). Equal 25-point weights; the thresholds
// are hand-set linear bands. Nothing in the composite is withheld.
export type CriComponent = {
  name: string;
  weight: string;
  inputs: string;
  read: string;
};

export const criComponents: CriComponent[] = [
  {
    name: "VIX",
    weight: "0 to 25",
    inputs: "Level plus 5-day rate of change",
    read: "The volatility complex is repricing",
  },
  {
    name: "VVIX",
    weight: "0 to 25",
    inputs: "Level plus the VVIX to VIX ratio",
    read: "Convexity on vol itself is being bid",
  },
  {
    name: "Correlation",
    weight: "0 to 25",
    inputs: "COR1M level plus its 5-session change",
    read: "Diversification is breaking down",
  },
  {
    name: "Momentum",
    weight: "0 to 25",
    inputs: "SPX distance below its 100-day moving average",
    read: "Trend followers flip from long to short",
  },
];

// The published signal bands, verbatim from the method note
// (docs/strategies.md, Signal Levels; cri_scan.py cri_level).
export type CriSignalBand = {
  range: string;
  level: string;
  meaning: string;
};

export const criSignalBands: CriSignalBand[] = [
  { range: "0 to 24", level: "Low", meaning: "Normal regime, no systematic risk" },
  {
    range: "25 to 49",
    level: "Elevated",
    meaning: "One or more components stressed, monitor",
  },
  {
    range: "50 to 74",
    level: "High",
    meaning: "Multiple components triggered, CTA selling likely imminent",
  },
  {
    range: "75 to 100",
    level: "Critical",
    meaning: "Full crash regime, active systematic deleveraging",
  },
];

export const crashRiskIndexFaqEntries: FaqEntry[] = [
  {
    question: "Is CRI the VIX?",
    answer:
      "No, although the VIX is one of its four components. The VIX is a single number: an index of 30-day implied volatility on the S&P 500. CRI composites four scores: VIX level and rate of change, VVIX and the VVIX to VIX ratio, COR1M implied correlation, and SPX distance from its 100-day moving average. A moderate VIX with correlation spiking and SPX below trend still moves the index. The two can disagree, and the disagreement is itself information.",
  },
  {
    question: "What inputs does CRI use?",
    answer:
      "Four components, each scored 0 to 25 on published linear bands: VIX, the level plus its 5-day rate of change; VVIX, the level plus the VVIX to VIX ratio; correlation, the Cboe 1-month implied correlation index COR1M plus its 5-session change; and momentum, SPX distance below its 100-day moving average. The four scores sum to the 0 to 100 index. The inputs, the equal weights, and the thresholds are all published.",
  },
  {
    question: "Does an elevated CRI mean sell everything?",
    answer:
      "No. An elevated band is a regime state, not a forecast of a date. In the published bands, 25 to 49 means one or more components is stressed and worth monitoring. Inside Radon's method it does two things: at the corroboration milestone it downgrades bullish flow signals that fight the regime, and in the strategy registry it prices the Crash Hedge play, defined-risk tail protection through a put debit spread when CRI enters the elevated band.",
  },
  {
    question: "Where can I see CRI?",
    answer:
      "In the free demo at demo.radon.run. CRI renders in the regime view alongside the other three regime models: GEX for dealer positioning, VCG-R for the volatility-credit gap, and GRG for gamma rotation.",
  },
];

// --- Metadata (clusterPageMetadata contract shape) -------------------------
// Next metadata merge is shallow: defining openGraph here replaces the layout
// object, so the shared social image must be re-included from lib/seo.ts.

export const crashRiskIndexMetadata: Metadata = {
  title: CRASH_RISK_INDEX_TITLE,
  description: CRASH_RISK_INDEX_DESCRIPTION,
  alternates: {
    canonical: `/${CRASH_RISK_INDEX_SLUG}`,
  },
  openGraph: {
    type: "article",
    url: `/${CRASH_RISK_INDEX_SLUG}`,
    title: CRASH_RISK_INDEX_TITLE,
    description: CRASH_RISK_INDEX_DESCRIPTION,
    siteName: SITE_NAME,
    locale: "en_US",
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
    title: CRASH_RISK_INDEX_TITLE,
    description: CRASH_RISK_INDEX_DESCRIPTION,
    images: [SOCIAL_IMAGE_PATH],
  },
};

// --- JSON-LD ---------------------------------------------------------------

const radonOrganization = {
  "@type": "Organization",
  name: "Radon",
  url: siteUrl,
} as const;

export function crashRiskIndexBreadcrumbStructuredData() {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      {
        "@type": "ListItem",
        position: 1,
        name: "Radon",
        item: siteUrl,
      },
      {
        "@type": "ListItem",
        position: 2,
        name: CRASH_RISK_INDEX_NAV_LABEL,
        item: crashRiskIndexUrl,
      },
    ],
  };
}

export function crashRiskIndexArticleStructuredData() {
  return {
    "@context": "https://schema.org",
    "@type": "TechArticle",
    headline: CRASH_RISK_INDEX_H1,
    description: CRASH_RISK_INDEX_DESCRIPTION,
    url: crashRiskIndexUrl,
    mainEntityOfPage: crashRiskIndexUrl,
    author: radonOrganization,
    publisher: radonOrganization,
    datePublished: CRASH_RISK_INDEX_DATE_PUBLISHED,
    dateModified: CRASH_RISK_INDEX_DATE_MODIFIED,
    isPartOf: {
      "@type": "WebSite",
      name: SITE_NAME,
      url: siteUrl,
    },
  };
}

// FAQPage schema is emitted only because the same entries render as visible
// FAQ rows on the page (mirrors lib/faq-content.ts faqStructuredData).
export function crashRiskIndexFaqStructuredData() {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: crashRiskIndexFaqEntries.map((entry) => ({
      "@type": "Question",
      name: entry.question,
      acceptedAnswer: {
        "@type": "Answer",
        text: entry.answer,
      },
    })),
  };
}

export function crashRiskIndexStructuredData() {
  return [
    crashRiskIndexBreadcrumbStructuredData(),
    crashRiskIndexArticleStructuredData(),
    crashRiskIndexFaqStructuredData(),
  ];
}
