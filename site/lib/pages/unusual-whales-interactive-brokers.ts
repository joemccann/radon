// Content module for /unusual-whales-interactive-brokers.
// Single source of truth for the page copy, the FAQ entries, and the JSON-LD
// builders, so the schema always mirrors the visible copy (same pattern as
// lib/faq-content.ts). Grounding: docs/evaluation.md (milestones, intraday
// interpolation, signal interpretation), root CLAUDE.md (data source priority,
// four gates), docs/unusual_whales_api.md (endpoint surface), and
// lib/editorial-content.ts (flowArgumentSteps, milestones).

import type { Metadata } from "next";
import type { Milestone } from "../editorial-content";
import type { FaqEntry, FaqPageSchema } from "../faq-content";
import {
  SITE_NAME,
  SOCIAL_IMAGE_ALT,
  SOCIAL_IMAGE_PATH,
  siteUrl,
} from "../seo";

export const SLUG = "unusual-whales-interactive-brokers";
export const NAV_LABEL = "Unusual Whales + Interactive Brokers";
export const PAGE_TITLE =
  "Unusual Whales + Interactive Brokers | Radon Terminal";
export const PAGE_DESCRIPTION =
  "How Unusual Whales flow becomes Interactive Brokers orders: dark pool prints scored, gated, sized, and routed as defined-risk combo orders. Free demo.";
export const PAGE_HEADLINE =
  "Unusual Whales prints in. Interactive Brokers orders out.";

// Frozen content dates (same rationale as SITE_CONTENT_LAST_MODIFIED in
// lib/seo.ts): update when the copy meaningfully changes, never request-time.
export const DATE_PUBLISHED = "2026-07-03";
export const DATE_MODIFIED = "2026-07-03";

export const pageUrl = `${siteUrl}/${SLUG}`;

// Mirrors the clusterPageMetadata({ slug, title, description }) contract shape.
// Built here because Next metadata merge is shallow: defining openGraph on a
// page replaces the layout object, so the shared OG image must be re-included.
// Integrator: swap to clusterPageMetadata from lib/seo.ts once the helper lands.
export const pageMetadata: Metadata = {
  title: PAGE_TITLE,
  description: PAGE_DESCRIPTION,
  alternates: { canonical: `/${SLUG}` },
  openGraph: {
    type: "article",
    url: `/${SLUG}`,
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

export type IngestSurface = {
  label: string;
  body: string;
};

// The UW surfaces the evaluation pipeline consumes, keyed to its milestones
// (docs/evaluation.md: M2 dark pool flow, M3 options flow, M3B OI changes
// required, M1B seasonality as context).
export const ingestSurfaces: IngestSurface[] = [
  {
    label: "Prints · M2",
    body:
      "Dark pool and OTC prints: the primary evidence of accumulation or distribution, scored before the lit price moves.",
  },
  {
    label: "Options flow · M3",
    body:
      "Sweeps and blocks, read for side dominance: ask-dominant flow is buying, bid-dominant flow is selling.",
  },
  {
    label: "OI changes · M3B",
    body:
      "Open-interest deltas confirm whether the flow opened new positions or closed old ones. Required, not optional.",
  },
  {
    label: "Seasonality · M1B",
    body:
      "Historical bias as context only. It informs the read; it never decides the trade, and strong flow overrides it by rule.",
  },
];

// The print-to-order pipeline, one row per stage, rendered in the
// MilestoneRow pattern (Milestone shape from lib/editorial-content.ts).
export const pipelineStages: Milestone[] = [
  {
    name: "Unusual Whales prints",
    body:
      "Off-exchange and OTC prints, sweeps, and options flow arrive from Unusual Whales and are checked against the Interactive Brokers realtime tape before anything is scored.",
    tag: "ingest · UW + IB tape",
  },
  {
    name: "Venue weighting",
    body:
      "Volume is venue-weighted and normalized to a rolling z-score. Directional pressure is inferred from print-side and size clustering, not from a single loud print.",
    tag: "score · z-score / clustering",
  },
  {
    name: "Flow score",
    body:
      "The output is a threshold-crossing flow score with a lead window measured per ticker. A score that misses its threshold is discarded, whatever the chart looks like.",
    tag: "signal · lead window",
  },
  {
    name: "Four gates",
    body:
      "Convexity requires gain of at least twice the loss with defined risk. Edge requires a specific signal that has not moved the lit price. Risk requires fractional Kelly under a hard cap. The fourth gate, a naked-short block, is disabled by operator policy with its logic preserved. Any failure stops the trade and names the gate.",
    tag: "gate · sequential",
  },
  {
    name: "Structure",
    body:
      "A defined-risk options structure is selected from the catalog to express the read with convexity. Multi-leg combos are preferred over single-leg directional bets.",
    tag: "convexity · defined risk",
  },
  {
    name: "Kelly size",
    body:
      "Fractional Kelly sets the position against bankroll, capped hard at 2.5% per position. Conviction adjusts within the cap, never past it.",
    tag: "risk · ≤ 2.5% bankroll",
  },
  {
    name: "Verify",
    body:
      "The mandatory pre-trade chokepoint computes max loss, margin impact, and naked-short exposure on the assembled combo. It is not optional and cannot be bypassed.",
    tag: "chokepoint · pre-trade",
  },
  {
    name: "Interactive Brokers order",
    body:
      "The cleared structure is assembled into an Interactive Brokers BAG combo order and submitted through the account's own routing.",
    tag: "execute · BAG combo",
  },
  {
    name: "Journal",
    body:
      "Every fill lands in an append-only journal with lot-matched profit and loss, preserving the full chain from print to position.",
    tag: "record · lot-matched P&L",
  },
];

export type RequirementRow = {
  component: string;
  supplies: string;
  requiredFor: string;
};

export const requirementRows: RequirementRow[] = [
  {
    component: "Unusual Whales subscription",
    supplies: "Dark pool prints, sweeps, options flow, OI changes",
    requiredFor: "Live flow scoring",
  },
  {
    component: "Interactive Brokers account, funded",
    supplies: "Realtime tape, margin, order routing",
    requiredFor: "Live tape and execution",
  },
  {
    component: "Radon demo · demo.radon.run",
    supplies: "Full instrument on seeded data",
    requiredFor: "Neither subscription",
  },
];

export const faqEntries: FaqEntry[] = [
  {
    question: "Do I need both subscriptions?",
    answer:
      "For live use, yes. Unusual Whales supplies the dark pool prints and options flow that scoring is built on, and a funded Interactive Brokers account supplies the realtime tape, margin, and order routing. The free demo at demo.radon.run requires neither.",
  },
  {
    question: "Can I act on Unusual Whales alerts without Radon?",
    answer:
      "You can. An alert is a read, and nothing stops a trader from acting on it directly. What Radon adds is the discipline between read and order: reconciliation against the Interactive Brokers tape, four sequential gates, fractional Kelly sizing hard-capped at 2.5% of bankroll per position, a mandatory pre-trade risk check, and a journaled audit trail.",
  },
  {
    question: "Which Unusual Whales data does Radon use?",
    answer:
      "Dark pool and OTC prints, options flow including sweeps, open-interest changes, and seasonality. Prints and options flow drive the flow score, open-interest changes are a required confirmation step, and seasonality is context that never gates a trade.",
  },
  {
    question: "Is Radon affiliated with Unusual Whales or Interactive Brokers?",
    answer:
      "No. Radon is a customer of both companies and nothing more. The Unusual Whales link on this page is a referral link, and it is disclosed as one.",
  },
];

export function faqStructuredData(): FaqPageSchema {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqEntries.map((entry) => ({
      "@type": "Question",
      name: entry.question,
      acceptedAnswer: {
        "@type": "Answer",
        text: entry.answer,
      },
    })),
  };
}

export function breadcrumbStructuredData() {
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "Radon", item: siteUrl },
      { "@type": "ListItem", position: 2, name: NAV_LABEL, item: pageUrl },
    ],
  } as const;
}

export function techArticleStructuredData() {
  const organization = {
    "@type": "Organization",
    name: "Radon",
    url: siteUrl,
  } as const;

  return {
    "@context": "https://schema.org",
    "@type": "TechArticle",
    headline: PAGE_HEADLINE,
    description: PAGE_DESCRIPTION,
    url: pageUrl,
    mainEntityOfPage: pageUrl,
    author: organization,
    publisher: organization,
    datePublished: DATE_PUBLISHED,
    dateModified: DATE_MODIFIED,
    isPartOf: {
      "@type": "WebSite",
      name: SITE_NAME,
      url: siteUrl,
    },
  } as const;
}

// The page's single JSON-LD payload: BreadcrumbList, TechArticle, and FAQPage.
// FAQPage is included only because the FAQ rows render visibly on the page.
export function pageStructuredData() {
  return [
    breadcrumbStructuredData(),
    techArticleStructuredData(),
    faqStructuredData(),
  ];
}
