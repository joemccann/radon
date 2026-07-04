// Content module for /defined-risk-options-structures.
// The catalog itself is read from docs/options-structures.json (the same
// file that drives the order-entry guard), so every count on the page is
// recomputed from the data at build time, never hand-copied. It is loaded
// with node:fs instead of a static import because turbopack.root is pinned
// to site/ and refuses module imports from outside it; the page is fully
// static, so this read happens at build/prerender time only.

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { SITE_NAME, siteUrl } from "../seo";

function loadCatalogJson(): unknown {
  // cwd is site/ under `next build`/`next dev`, but the repo root under the
  // root Vitest run, so both locations are tried.
  const candidates = [
    path.join(process.cwd(), "..", "docs", "options-structures.json"),
    path.join(process.cwd(), "docs", "options-structures.json"),
  ];
  const file = candidates.find((candidate) => existsSync(candidate));
  if (!file) {
    throw new Error(
      `options-structures.json not found; looked in: ${candidates.join(", ")}`,
    );
  }
  return JSON.parse(readFileSync(file, "utf8"));
}

const catalogJson = loadCatalogJson();

export type CatalogLeg = {
  type: string;
  action: string;
  strike: string | null;
  expiry: string;
};

export type CatalogStructure = {
  name: string;
  aliases: string[];
  category: string;
  legs: CatalogLeg[];
  bias: string;
  risk_profile: string;
  max_gain: string;
  max_loss: string;
  guard_decision: string;
  guard_reason: string;
  guard_gap: string | null;
  notes: string | null;
};

export const catalog = catalogJson as CatalogStructure[];

export const SLUG = "defined-risk-options-structures";
export const NAV_LABEL = "Structure Catalog";
export const PAGE_TITLE =
  "Defined-Risk Options Structure Catalog | Radon Terminal";
export const H1_TEXT =
  "Fifty-eight structures. Thirty-seven clear the defined-risk gate.";

// Frozen content dates for the TechArticle schema. Update on meaningful copy
// or catalog changes; a request-time stamp gets ignored (same rationale as
// SITE_CONTENT_LAST_MODIFIED in lib/seo.ts).
export const PAGE_DATE_PUBLISHED = "2026-07-03";
export const PAGE_DATE_MODIFIED = "2026-07-03";

function isConditional(structure: CatalogStructure): boolean {
  return (
    structure.risk_profile !== "defined" &&
    structure.risk_profile !== "undefined"
  );
}

export const catalogCounts = {
  total: catalog.length,
  defined: catalog.filter((s) => s.risk_profile === "defined").length,
  undefinedRisk: catalog.filter((s) => s.risk_profile === "undefined").length,
  conditional: catalog.filter(isConditional).length,
  allowed: catalog.filter((s) => s.guard_decision === "ALLOW").length,
  blocked: catalog.filter((s) => s.guard_decision === "BLOCK").length,
  undefinedAllowed: catalog.filter(
    (s) => s.guard_decision === "ALLOW" && s.risk_profile === "undefined",
  ).length,
  families: new Set(catalog.map((s) => s.category)).size,
};

export const META_DESCRIPTION = `The full catalog: ${catalogCounts.total} options structures with legs, bias, max gain, max loss, and a defined or undefined risk verdict for each. ${catalogCounts.defined} clear Radon's convexity gate.`;

export type StructureFamily = {
  id: string;
  label: string;
  structures: CatalogStructure[];
};

const FAMILY_ORDER: { id: string; label: string }[] = [
  { id: "single", label: "Single leg" },
  { id: "vertical", label: "Verticals" },
  { id: "horizontal", label: "Horizontals and calendars" },
  { id: "straddle", label: "Straddles" },
  { id: "strangle", label: "Strangles" },
  { id: "butterfly", label: "Butterflies" },
  { id: "condor", label: "Condors" },
  { id: "ratio", label: "Ratio spreads" },
  { id: "synthetic", label: "Synthetics" },
  { id: "covered", label: "Covered and protective" },
  { id: "collar", label: "Collars" },
  { id: "complex", label: "Complex and exotic" },
];

export const structureFamilies: StructureFamily[] = FAMILY_ORDER.map(
  (family) => ({
    ...family,
    structures: catalog.filter((s) => s.category === family.id),
  }),
);

// The honesty exhibit: structures the leg-matching ladder allows even though
// the catalog marks their risk profile undefined. Three of these (Long Risk
// Reversal, Jade Lizard, Seagull Spread) are the cases docs/options-structures.md
// documents under Guard Gaps as insufficient for simple leg matching.
export const undefinedAllowedStructures = catalog.filter(
  (s) => s.guard_decision === "ALLOW" && s.risk_profile === "undefined",
);

export function formatLegs(structure: CatalogStructure): string {
  return structure.legs
    .map((leg) => {
      const strike =
        leg.strike && leg.strike !== "N/A" ? ` (${leg.strike})` : "";
      const expiry =
        leg.expiry !== "standard" && leg.expiry !== "N/A"
          ? ` [${leg.expiry}]`
          : "";
      return `${leg.action} ${leg.type.toUpperCase()}${strike}${expiry}`;
    })
    .join(" + ");
}

export function formatBias(bias: string): string {
  return bias.replace(/_/g, " ");
}

export type RiskVerdict = "defined" | "undefined" | "conditional";

export function riskVerdict(structure: CatalogStructure): RiskVerdict {
  if (structure.risk_profile === "defined") return "defined";
  if (structure.risk_profile === "undefined") return "undefined";
  return "conditional";
}

// Guard decision ladder, verbatim from docs/options-structures.md
// (Guard Decision Logic), with the em-dash annotations rewritten per the
// site copy convention.
export type GuardLadderRule = {
  rule: string;
  decision: "ALLOW" | "BLOCK" | "GAP";
  note: string;
};

export const guardLadder: GuardLadderRule[] = [
  { rule: "BUY anything", decision: "ALLOW", note: "no short exposure" },
  { rule: "SELL put", decision: "ALLOW", note: "cash-secured, defined risk" },
  {
    rule: "Combo: BUY call + SELL call",
    decision: "ALLOW",
    note: "vertical spread",
  },
  {
    rule: "Combo: BUY call + SELL put",
    decision: "ALLOW",
    note: "risk reversal: treated as defined",
  },
  {
    rule: "Combo: SELL call + BUY put",
    decision: "GAP",
    note: "synthetic short: naked call",
  },
  {
    rule: "SELL call, no stock",
    decision: "BLOCK",
    note: "naked short call",
  },
  {
    rule: "SELL call, insufficient stock",
    decision: "BLOCK",
    note: "short a tail",
  },
  {
    rule: "SELL stock, no shares",
    decision: "BLOCK",
    note: "naked short stock",
  },
  {
    rule: "SELL stock, quantity above held",
    decision: "BLOCK",
    note: "oversell",
  },
];

// Registry plays and the catalog structures they select at milestone 5
// (lib/editorial-content.ts registryEntries; docs/evaluation.md milestone 5).
export type PlayMapping = {
  play: string;
  structure: string;
  family: string;
};

export const registryStructureMap: PlayMapping[] = [
  { play: "Dark Lead", structure: "Call debit spread", family: "vertical" },
  { play: "Gamma Pin", structure: "Iron condor", family: "condor" },
  { play: "Panic Gap", structure: "Put backspread", family: "ratio" },
  {
    play: "Rotation Front",
    structure: "Call calendar",
    family: "horizontal",
  },
  { play: "Crash Hedge", structure: "Put debit spread", family: "vertical" },
  { play: "LEAP Mispricing", structure: "Long LEAP call", family: "single" },
];

export type FaqEntry = {
  question: string;
  answer: string;
};

export const faqEntries: FaqEntry[] = [
  {
    question: "What counts as defined risk in this catalog?",
    answer: `A structure is defined risk when its maximum loss is computable from the structure alone, before the order exists: the debit paid, the strike width minus the credit received, or a strike-backed collateral figure. Of the ${catalogCounts.total} catalog entries, ${catalogCounts.defined} meet that test, ${catalogCounts.undefinedRisk} do not, and ${catalogCounts.conditional} are conditional on how strikes and tenors are set. Defined risk is the precondition for Radon's Gate 1, which requires gain of at least 2 times loss.`,
  },
  {
    question: "Why catalog undefined-risk structures at all?",
    answer: `You cannot block what you have not named. The order-entry guard decides ALLOW or BLOCK by matching legs against this taxonomy, so the ${catalogCounts.undefinedRisk} undefined-risk entries have to be in the catalog for the guard to reason about them. ${catalogCounts.undefinedAllowed} of them the guard allows anyway, on the convention that a sold put is cash-secured, and the catalog reference documents the hard cases among those in its own words. An honest catalog records what the system rejects and where its logic leans on assumptions, not only what it trades.`,
  },
  {
    question: "Is a cash-secured put defined risk?",
    answer:
      "Yes, by the catalog's definition. The Short Put (Cash-Secured) entry is marked defined with a max loss of strike times 100 per contract, which is the collateral requirement the catalog records in its notes. The worst case is assignment with the stock at zero: a large number, but one that is known at submit. Defined risk means known loss, not small loss.",
  },
  {
    question: "Why is Gate 4 marked disabled?",
    answer:
      "Gate 4, the no-naked-shorts gate, was disabled by operator policy on 2026-04-30. The blocking logic is preserved in the codebase rather than deleted, and the re-enable path is documented, so the gate can be restored without rebuilding it. The catalog's guard decisions on this page describe that preserved logic, exactly as the gates card on the homepage states.",
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

export function faqStructuredData() {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqEntries.map(
      (entry): FaqQuestionSchema => ({
        "@type": "Question",
        name: entry.question,
        acceptedAnswer: {
          "@type": "Answer",
          text: entry.answer,
        },
      }),
    ),
  } as const;
}

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
        name: NAV_LABEL,
        item: `${siteUrl}/${SLUG}`,
      },
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
    headline: H1_TEXT,
    description: META_DESCRIPTION,
    url: `${siteUrl}/${SLUG}`,
    mainEntityOfPage: `${siteUrl}/${SLUG}`,
    author: organization,
    publisher: organization,
    datePublished: PAGE_DATE_PUBLISHED,
    dateModified: PAGE_DATE_MODIFIED,
    isPartOf: {
      "@type": "WebSite",
      name: SITE_NAME,
      url: siteUrl,
    },
  } as const;
}

export function pageStructuredData() {
  return [
    breadcrumbStructuredData(),
    techArticleStructuredData(),
    faqStructuredData(),
  ];
}
