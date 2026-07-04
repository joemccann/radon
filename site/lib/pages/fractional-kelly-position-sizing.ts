// Content module for /fractional-kelly-position-sizing.
// Typed copy, FAQ entries, worked-example rows, per-page constants, and
// JSON-LD builders. Single source of truth so the schema always mirrors the
// visible copy (same pattern as lib/faq-content.ts).

import type { Metadata } from "next";
import type { FaqEntry, FaqPageSchema } from "../faq-content";
import {
  SITE_NAME,
  SOCIAL_IMAGE_ALT,
  SOCIAL_IMAGE_PATH,
  siteUrl,
} from "../seo";

export const PAGE_SLUG = "fractional-kelly-position-sizing";
export const PAGE_NAV_LABEL = "Fractional Kelly position sizing";
export const PAGE_TITLE =
  "Fractional Kelly Options Position Sizing | Radon Terminal";
export const PAGE_DESCRIPTION =
  "How Radon sizes options positions: fractional Kelly from structure max loss and signal odds, hard-capped at 2.5% of bankroll per position. Free demo.";
export const PAGE_HEADLINE = "Position size is a policy, not a feeling.";

// Frozen per-page dates for TechArticle JSON-LD. Update dateModified when the
// copy meaningfully changes; a request-time stamp gets ignored by Google
// (same rationale as SITE_CONTENT_LAST_MODIFIED in lib/seo.ts).
export const PAGE_DATE_PUBLISHED = "2026-07-03";
export const PAGE_DATE_MODIFIED = "2026-07-03";

export const pageUrl = `${siteUrl}/${PAGE_SLUG}`;

// Worked example for the sizing plate. Derived at authoring time:
// b = 1500 / 500 = 3.0; f = 0.45 - (0.55 / 3.0) = 0.45 - 0.1833 = 0.2667;
// quarter Kelly = 0.2667 / 4 = 6.7%; the 2.5% cap binds.
export type WorkedExampleRow = {
  step: string;
  value: string;
};

export const workedExampleRows: WorkedExampleRow[] = [
  { step: "Structure", value: "Call debit spread · defined risk" },
  { step: "Max loss", value: "Debit paid = $500" },
  { step: "Max gain", value: "Width minus debit = $1,500" },
  { step: "Net odds", value: "b = 1,500 / 500 = 3.0" },
  { step: "Signal probability", value: "p = 0.45" },
  {
    step: "Full Kelly",
    value: "f = 0.45 - (0.55 / 3.0) = 0.2667 = 26.7%",
  },
  { step: "Quarter Kelly", value: "0.2667 / 4 = 6.7%" },
  { step: "Radon cap", value: "min(6.7%, 2.5%) = 2.5% of bankroll · cap binds" },
];

export const kellySizingFaqEntries: FaqEntry[] = [
  {
    question: "Why cap position size below what full Kelly allows?",
    answer:
      "Because the probability feeding the formula is an estimate built from flow data, not a known quantity. Overestimating it with full Kelly produces overbetting, and overbetting is the failure mode that compounds: it deepens drawdowns and can turn a positive edge into a losing strategy. A fraction of Kelly under a fixed ceiling gives up some theoretical growth to stay solvent against estimation error.",
  },
  {
    question: "What if Kelly suggests less than 2.5%?",
    answer:
      "Take the smaller number. The cap is a ceiling, not a target. When fractional Kelly on a weak or downgraded signal works out to less than 2.5% of bankroll, that smaller figure is the position size. Nothing in the policy rounds a small position up to the cap.",
  },
  {
    question: "Does Radon size positions automatically?",
    answer:
      "It computes the size and enforces the cap. Fractional Kelly is calculated at the sizing milestone from the structure's maximum loss and the signal's odds, and the mandatory pre-trade check verifies maximum loss and margin impact on the assembled combo before anything routes. Conviction can adjust size within the cap; it cannot route past it.",
  },
  {
    question: "Can I override the 2.5% cap?",
    answer:
      "No. A hard cap that can be overridden is advisory, and the evaluation pipeline is explicit that this one is not: milestone 6 enforces the cap before milestone 7 will route an order. The value of the ceiling is that it holds on exactly the trade where overriding it feels most justified.",
  },
];

export function kellyFaqStructuredData(): FaqPageSchema {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: kellySizingFaqEntries.map((entry) => ({
      "@type": "Question",
      name: entry.question,
      acceptedAnswer: {
        "@type": "Answer",
        text: entry.answer,
      },
    })),
  };
}

const radonOrganization = {
  "@type": "Organization",
  name: "Radon",
  url: siteUrl,
} as const;

export function breadcrumbStructuredData() {
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
        name: PAGE_NAV_LABEL,
        item: pageUrl,
      },
    ],
  };
}

export function techArticleStructuredData() {
  return {
    "@context": "https://schema.org",
    "@type": "TechArticle",
    headline: PAGE_HEADLINE,
    description: PAGE_DESCRIPTION,
    url: pageUrl,
    mainEntityOfPage: pageUrl,
    author: radonOrganization,
    publisher: radonOrganization,
    datePublished: PAGE_DATE_PUBLISHED,
    dateModified: PAGE_DATE_MODIFIED,
    isPartOf: {
      "@type": "WebSite",
      name: SITE_NAME,
      url: siteUrl,
    },
  };
}

// Exactly BreadcrumbList + TechArticle + FAQPage (FAQPage only because the
// FAQ rows render visibly on the page).
export function pageStructuredData() {
  return [
    breadcrumbStructuredData(),
    techArticleStructuredData(),
    kellyFaqStructuredData(),
  ];
}

// Contract shape for clusterPageMetadata({ slug, title, description }).
// Built here until the shared helper lands in lib/seo.ts; the openGraph block
// re-includes the shared image because Next's metadata merge is shallow.
export function kellyPageMetadata(): Metadata {
  return {
    title: PAGE_TITLE,
    description: PAGE_DESCRIPTION,
    alternates: {
      canonical: `/${PAGE_SLUG}`,
    },
    openGraph: {
      type: "article",
      url: `/${PAGE_SLUG}`,
      title: PAGE_TITLE,
      description: PAGE_DESCRIPTION,
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
      title: PAGE_TITLE,
      description: PAGE_DESCRIPTION,
      images: [SOCIAL_IMAGE_PATH],
    },
  };
}
