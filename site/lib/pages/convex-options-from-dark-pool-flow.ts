// Content module for the /convex-options-from-dark-pool-flow cluster page.
// Single source of truth for the page copy, the FAQ, and the JSON-LD so the
// schema always mirrors the visible content (same pattern as lib/faq-content.ts).
//
// Grounding: docs/evaluation.md (milestones, interpolation, signal bands),
// root CLAUDE.md (Four Gates), lib/editorial-content.ts (registry entries).

import type { Metadata } from "next";
import {
  SITE_NAME,
  SOCIAL_IMAGE_ALT,
  SOCIAL_IMAGE_PATH,
  siteUrl,
} from "../seo";

export const CLUSTER_SLUG = "convex-options-from-dark-pool-flow";
export const NAV_LABEL = "Convex options from dark pool flow";
export const PAGE_TITLE =
  "Convex Options Trades From Dark Pool Flow | Radon Terminal";
export const PAGE_DESCRIPTION =
  "The seven-milestone method that turns a dark pool print into a convex, defined-risk options trade, with the thresholds and the stop rules stated.";
export const PAGE_H1 =
  "The flow finds the edge. The structure makes it convex.";

// Frozen content dates for the TechArticle schema. Update on meaningful copy
// changes; a request-time stamp gets ignored (same rationale as
// SITE_CONTENT_LAST_MODIFIED in lib/seo.ts).
export const DATE_PUBLISHED = "2026-07-03";
export const DATE_MODIFIED = "2026-07-03";

// Mirrors the clusterPageMetadata helper specified for lib/seo.ts. Kept here
// so this page ships without editing shared files; the integrator can lift it
// into lib/seo.ts verbatim. Next metadata merge is shallow: defining openGraph
// on a page replaces the layout object, so images are re-included from the
// shared constants.
export function clusterPageMetadata({
  slug,
  title,
  description,
}: {
  slug: string;
  title: string;
  description: string;
}): Metadata {
  return {
    title,
    description,
    alternates: { canonical: `/${slug}` },
    openGraph: {
      type: "article",
      url: `/${slug}`,
      title,
      description,
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
      title,
      description,
      images: [SOCIAL_IMAGE_PATH],
    },
  };
}

// ---------- S2 · The four gates (root CLAUDE.md, verbatim in substance) ----------

export type GateRow = {
  no: string;
  name: string;
  rule: string;
  condition: string;
  disabled?: boolean;
};

export const gateRows: GateRow[] = [
  {
    no: "01",
    name: "Convexity",
    rule: "gain ≥ 2× loss",
    condition:
      "Defined-risk structures only. The capped loss is known before the order is submitted.",
  },
  {
    no: "02",
    name: "Edge",
    rule: "signal precedes price",
    condition:
      "A specific, data-backed dark-pool or OTC signal that has not yet moved the lit price.",
  },
  {
    no: "03",
    name: "Risk",
    rule: "≤ 2.5% bankroll",
    condition:
      "Fractional Kelly sizing with a hard per-position ceiling. The cap is enforced, not advisory.",
  },
  {
    no: "04",
    name: "Naked shorts",
    rule: "no naked shorts",
    condition:
      "Disabled by operator policy on 2026-04-30. The blocking logic is preserved for re-enable.",
    disabled: true,
  },
];

// ---------- S3 · Seven milestones (docs/evaluation.md, verbatim in substance) ----------

export type MilestoneDetail = {
  name: string;
  body: string;
  tag: string;
  reference?: { href: string; label: string };
};

export const milestoneDetails: MilestoneDetail[] = [
  {
    name: "Validate the ticker",
    body:
      "Seasonality, analyst ratings, and news are pulled first, and all three are context, not gates. A favorable seasonal window never authorizes a trade on its own, and an unfavorable one never vetoes strong flow. The milestone exists to frame the candidate before the flow evidence is read.",
    tag: "context · seasonality / ratings / news",
  },
  {
    name: "Read the dark pool flow",
    body:
      "Off-exchange prints are aggregated for the candidate. During market hours the partial session is volume-weighted interpolated against the prior five-day average, and both the actual and the interpolated figures are always output: the math is published in full below. The read is also checked against the tail-risk regime, because a signal fighting the regime is downgraded, not ignored.",
    tag: "flow · actual + interpolated",
    reference: { href: "/crash-risk-index", label: "The Crash Risk Index" },
  },
  {
    name: "Read the options flow and OI changes",
    body:
      "The options tape is read next: put/call balance, the side of the print, and the open-interest changes that show whether positioning actually followed the paper. The open-interest check is a required step in the method, not an optional one.",
    tag: "flow · options + open interest",
  },
  {
    name: "Make the edge decision",
    body:
      "A binary PASS or FAIL on the edge gate. FAIL means stop: no structure is built, no Kelly arithmetic is run, and no trade is logged. The decision itself is still recorded as NO_TRADE, because a pass on discipline is evidence too.",
    tag: "gate · pass / fail",
  },
  {
    name: "Select the structure",
    body:
      "A defined-risk options structure is selected to express the read with convexity. Reward-to-risk below 2:1 is a stop, not a discount, and the trade specification is written down at this milestone before any sizing happens.",
    tag: "convexity · R:R ≥ 2:1",
    reference: {
      href: "/defined-risk-options-structures",
      label: "The defined-risk structure catalog",
    },
  },
  {
    name: "Size with fractional Kelly",
    body:
      "Fractional Kelly sets the position against bankroll with a hard cap of 2.5% per position. Conviction adjusts size within the ceiling and never past it. Sizing runs only on a structure that already cleared the edge and convexity checks.",
    tag: "risk · ≤ 2.5% bankroll",
    reference: {
      href: "/fractional-kelly-position-sizing",
      label: "The fractional Kelly sizing method",
    },
  },
  {
    name: "Log the decision",
    body:
      "Executed trades append to a journal that is append-only by design, so the record cannot be rewritten after the fact. NO_TRADE decisions are recorded as well. Routing itself runs through Interactive Brokers, and the audit trail keeps the full chain from print to order.",
    tag: "record · append-only journal",
    reference: {
      href: "/interactive-brokers-dark-pool-terminal",
      label: "How orders route through Interactive Brokers",
    },
  },
];

// ---------- S4 · Intraday interpolation (docs/evaluation.md, verbatim) ----------

export type InterpolationFormula = {
  label: string;
  expression: string;
};

export const interpolationFormulas: InterpolationFormula[] = [
  { label: "progress", expression: "minutes since 9:30 ET / 390" },
  { label: "projected", expression: "actual / progress" },
  {
    label: "blend",
    expression: "(projected × progress) + (prior 5d avg × (1 − progress))",
  },
  { label: "pace", expression: "actual / (avg prior × progress)" },
];

export type ConfidenceBand = {
  progress: string;
  confidence: string;
  priorWeight: string;
};

export const confidenceBands: ConfidenceBand[] = [
  { progress: "0–25%", confidence: "VERY_LOW", priorWeight: "75%+" },
  { progress: "25–50%", confidence: "LOW", priorWeight: "50–75%" },
  { progress: "50–75%", confidence: "MEDIUM", priorWeight: "25–50%" },
  { progress: "75–100%", confidence: "HIGH", priorWeight: "<25%" },
];

export const interpolationRules: string[] = [
  "The interpolated figure feeds the edge decision; the actual figure is reported next to it, always.",
  "LOW or VERY_LOW confidence: re-evaluate after 2 PM ET rather than force a call on thin data.",
  "Pace above 1.2× the prior average says the flow is real, not an artifact of the projection.",
  "Actual flow running opposite the prior average suggests a reversal, not a continuation.",
];

// ---------- S5 · Signal interpretation bands (docs/evaluation.md, verbatim) ----------

export type SignalBandRow = {
  signal: string;
  bands: { range: string; read: string }[];
};

export const signalBands: SignalBandRow[] = [
  {
    signal: "P/C ratio",
    bands: [
      { range: ">2.0", read: "BEAR" },
      { range: "1.2–2.0", read: "LEAN_BEAR" },
      { range: "0.8–1.2", read: "NEUTRAL" },
      { range: "0.5–0.8", read: "LEAN_BULL" },
      { range: "<0.5", read: "BULL" },
    ],
  },
  {
    signal: "Flow side",
    bands: [
      { range: "ask-dominant", read: "BUYING" },
      { range: "bid-dominant", read: "SELLING" },
    ],
  },
  {
    signal: "Analyst buy%",
    bands: [
      { range: "≥70%", read: "BULL" },
      { range: "50–69%", read: "LEAN_BULL" },
      { range: "30–49%", read: "LEAN_BEAR" },
      { range: "<30%", read: "BEAR" },
    ],
  },
  {
    signal: "Seasonality",
    bands: [
      { range: ">60%", read: "FAVORABLE" },
      { range: "50–60%", read: "NEUTRAL" },
      { range: "<50%", read: "UNFAVORABLE" },
    ],
  },
];

// ---------- S6 · The six plays (lib/editorial-content.ts registryEntries) ----------

export type PlayRow = {
  name: string;
  href?: string;
  edge: string;
  structure: string;
  convexityFloor: string;
};

export const playRows: PlayRow[] = [
  {
    name: "Dark Lead",
    edge: "Off-exchange accumulation",
    structure: "Call debit spread",
    convexityFloor: "≥ 3.0×",
  },
  {
    name: "Gamma Pin",
    edge: "Dealer gamma walls",
    structure: "Iron condor",
    convexityFloor: "≥ 2.2×",
  },
  {
    name: "Panic Gap",
    edge: "Widening VCG-R",
    structure: "Put backspread",
    convexityFloor: "≥ 4.0×",
  },
  {
    name: "Rotation Front",
    edge: "GRG sector migration",
    structure: "Call calendar",
    convexityFloor: "≥ 2.5×",
  },
  {
    name: "Crash Hedge",
    href: "/crash-risk-index",
    edge: "Elevated CRI",
    structure: "Put debit spread",
    convexityFloor: "≥ 5.0×",
  },
  {
    name: "LEAP Mispricing",
    edge: "IV term mispricing",
    structure: "Long LEAP call",
    convexityFloor: "≥ 2.0×",
  },
];

// ---------- S7 · FAQ (visible rows and FAQPage JSON-LD share this array) ----------

export type FaqEntry = {
  question: string;
  answer: string;
};

export const faqEntries: FaqEntry[] = [
  {
    question: "What happens when milestone 4 fails?",
    answer:
      "The evaluation stops. FAIL at the edge decision means no structure is built, no Kelly sizing is run, and no trade is logged. The decision itself is still recorded as NO_TRADE, so the record keeps evidence of discipline as well as evidence of trades.",
  },
  {
    question: "How long is the lead window?",
    answer:
      "It is measured per ticker, not assumed. The scanner surfaces the lead window alongside the flow score, and the window is treated as a property of the name and its flow history rather than a constant of the method.",
  },
  {
    question: "Why interpolate intraday flow at all?",
    answer:
      "Because a raw partial-session total is misleading at noon and nearly meaningless at 9:45. The method projects the session from elapsed minutes, blends the projection with the prior five-day average, grades its own confidence by session progress, and always outputs both the actual and the interpolated figures.",
  },
  {
    question: "Does the method predict direction?",
    answer:
      "No. It measures positioning: where off-exchange volume is accumulating, which side of the tape the options flow prints on, and whether open interest followed. Structure selection and Kelly sizing then price the risk of expressing that read. Convexity, not prediction, carries the trade.",
  },
];

// ---------- JSON-LD builders (schema mirrors visible copy) ----------

const pageUrl = `${siteUrl}/${CLUSTER_SLUG}`;

const organization = {
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
    headline: PAGE_H1,
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
  };
}

export function faqStructuredData() {
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

export function pageStructuredData() {
  return [
    breadcrumbStructuredData(),
    techArticleStructuredData(),
    faqStructuredData(),
  ];
}
