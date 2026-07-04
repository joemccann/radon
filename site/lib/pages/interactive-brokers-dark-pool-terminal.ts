// Content module for /interactive-brokers-dark-pool-terminal.
// Single source of truth for the page copy, FAQ entries, table rows,
// metadata, and JSON-LD builders so the schema always mirrors visible copy.
//
// Grounding: root CLAUDE.md (Data Source Priority, Architecture, Four Gates,
// Trades canonical store), docs/evaluation.md (7 milestones, hard 2.5% cap),
// docs/unusual_whales_api.md (endpoint surface), lib/editorial-content.ts
// (flowArgumentSteps, milestones), lib/faq-content.ts (broker requirements).

import type { Metadata } from "next";
import {
  SITE_NAME,
  SOCIAL_IMAGE_ALT,
  SOCIAL_IMAGE_PATH,
  siteUrl,
} from "../seo";

export const CLUSTER_SLUG = "interactive-brokers-dark-pool-terminal";
export const NAV_LABEL = "Interactive Brokers dark pool terminal";
export const PAGE_TITLE =
  "Interactive Brokers Dark Pool Terminal | Radon Terminal";
export const PAGE_DESCRIPTION =
  "A dark pool terminal on Interactive Brokers rails: off-exchange prints scored for accumulation, then routed as defined-risk IB combo orders. Free demo.";
export const PAGE_HEADLINE =
  "A dark pool terminal is only finished when it routes the order.";

// Frozen per-page dates for TechArticle JSON-LD. Update dateModified when the
// copy meaningfully changes; a request-time stamp gets ignored by Google
// (same rationale as SITE_CONTENT_LAST_MODIFIED in lib/seo.ts).
export const PAGE_DATE_PUBLISHED = "2026-07-03";
export const PAGE_DATE_MODIFIED = "2026-07-03";

export type IbRole = {
  role: string;
  supplies: string;
  terminalUse: string;
};

// The four jobs Interactive Brokers does inside the terminal.
// Grounds: root CLAUDE.md Data Source Priority + Trades canonical store;
// editorial-content.ts milestones Size / Verify / Route.
export const ibRoles: IbRole[] = [
  {
    role: "Realtime tape",
    supplies: "Realtime market data across equities and options",
    terminalUse:
      "Reconciliation source for the Unusual Whales off-exchange prints. First in the data source priority.",
  },
  {
    role: "Account state",
    supplies: "Bankroll, open positions, margin",
    terminalUse:
      "Feeds fractional Kelly sizing against the hard 2.5% of bankroll per-position cap.",
  },
  {
    role: "Execution",
    supplies: "Multi-leg combo (BAG) order routing",
    terminalUse:
      "Cleared structures are assembled into combo orders only after the mandatory pre-trade risk chokepoint.",
  },
  {
    role: "Journal",
    supplies: "Fills and lot data",
    terminalUse:
      "Every fill is journaled with lot-matched P&L. The journal is the canonical trade store; portfolio and order views derive from it.",
  },
];

export type RequirementRow = {
  item: string;
  liveUse: string;
  demo: string;
};

// Grounds: lib/faq-content.ts (broker requirements, demo scope).
export const requirementRows: RequirementRow[] = [
  {
    item: "Funded Interactive Brokers account",
    liveUse: "Required. Radon is not a broker; orders execute in your account.",
    demo: "Not required",
  },
  {
    item: "Unusual Whales subscription",
    liveUse: "Required. Supplies the live dark pool prints and options flow.",
    demo: "Not required",
  },
  {
    item: "TWS or IB Gateway session",
    liveUse: "Required. Carries the realtime tape and the order routing.",
    demo: "Not required",
  },
];

export type FaqEntry = {
  question: string;
  answer: string;
};

export const faqEntries: FaqEntry[] = [
  {
    question: "Does Radon show Interactive Brokers dark pool data?",
    answer:
      "No. Interactive Brokers does not supply Radon's dark pool prints, and Radon does not claim otherwise. The off-exchange and OTC prints come from Unusual Whales and are reconciled against the Interactive Brokers realtime tape before any flow score is computed.",
  },
  {
    question: "Can Radon place orders in my Interactive Brokers account?",
    answer:
      "Yes. A structure that clears every milestone is assembled into an Interactive Brokers combo order and submitted after the mandatory pre-trade risk check, which computes max loss and margin impact on the assembled combo. Only defined-risk structures route, and every fill is journaled with lot-matched P&L.",
  },
  {
    question: "Do I need TWS or IB Gateway running?",
    answer:
      "For live use, yes. Radon's realtime data and execution run through an Interactive Brokers TWS or IB Gateway session. The free demo at demo.radon.run runs on seeded data and needs no brokerage connection at all.",
  },
  {
    question: "Is Radon a broker?",
    answer:
      "No. Radon is a market-structure research instrument that runs on Interactive Brokers rails. Orders execute in your own Interactive Brokers account under its permissions and margin rules, and live trading requires a funded account there.",
  },
];

export type FaqQuestionSchema = {
  "@type": "Question";
  name: string;
  acceptedAnswer: {
    "@type": "Answer";
    text: string;
  };
};

export type FaqPageSchema = {
  "@context": "https://schema.org";
  "@type": "FAQPage";
  mainEntity: FaqQuestionSchema[];
};

// FAQPage JSON-LD is emitted only because the FAQ rows above render visibly
// on the page; the schema mirrors the entries array exactly.
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

const pageUrl = `${siteUrl}/${CLUSTER_SLUG}`;

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
      { "@type": "ListItem", position: 1, name: "Radon", item: siteUrl },
      { "@type": "ListItem", position: 2, name: NAV_LABEL, item: pageUrl },
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

export function pageStructuredData() {
  return [
    breadcrumbStructuredData(),
    techArticleStructuredData(),
    faqStructuredData(),
  ];
}

// Mirrors the clusterPageMetadata({ slug, title, description }) contract from
// lib/seo.ts. Kept local so this page ships without editing shared files; the
// integrator can swap this for the shared helper without changing output.
// openGraph must re-include the shared image because Next's metadata merge is
// shallow: defining openGraph here replaces the layout object entirely.
export function pageMetadata(): Metadata {
  return {
    title: PAGE_TITLE,
    description: PAGE_DESCRIPTION,
    alternates: {
      canonical: `/${CLUSTER_SLUG}`,
    },
    openGraph: {
      type: "article",
      url: `/${CLUSTER_SLUG}`,
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
