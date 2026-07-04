// Single cluster registry for the radon.run page cluster.
// Consumed by app/sitemap.ts, the homepage Dossiers index block, and any
// future surface that needs the full route list. Each entry re-exports the
// page's own frozen constants so slug/title/description/lastModified have
// exactly one source of truth: the page content module.

import {
  CLUSTER_SLUG as CONVEX_SLUG,
  DATE_MODIFIED as CONVEX_LAST_MODIFIED,
  NAV_LABEL as CONVEX_NAV_LABEL,
  PAGE_DESCRIPTION as CONVEX_DESCRIPTION,
  PAGE_TITLE as CONVEX_TITLE,
} from "./pages/convex-options-from-dark-pool-flow";
import {
  CRASH_RISK_INDEX_DATE_MODIFIED,
  CRASH_RISK_INDEX_DESCRIPTION,
  CRASH_RISK_INDEX_NAV_LABEL,
  CRASH_RISK_INDEX_SLUG,
  CRASH_RISK_INDEX_TITLE,
} from "./pages/crash-risk-index";
import {
  META_DESCRIPTION as CATALOG_DESCRIPTION,
  NAV_LABEL as CATALOG_NAV_LABEL,
  PAGE_DATE_MODIFIED as CATALOG_LAST_MODIFIED,
  PAGE_TITLE as CATALOG_TITLE,
  SLUG as CATALOG_SLUG,
} from "./pages/defined-risk-options-structures";
import {
  PAGE_DATE_MODIFIED as KELLY_LAST_MODIFIED,
  PAGE_DESCRIPTION as KELLY_DESCRIPTION,
  PAGE_NAV_LABEL as KELLY_NAV_LABEL,
  PAGE_SLUG as KELLY_SLUG,
  PAGE_TITLE as KELLY_TITLE,
} from "./pages/fractional-kelly-position-sizing";
import {
  CLUSTER_SLUG as IB_TERMINAL_SLUG,
  NAV_LABEL as IB_TERMINAL_NAV_LABEL,
  PAGE_DATE_MODIFIED as IB_TERMINAL_LAST_MODIFIED,
  PAGE_DESCRIPTION as IB_TERMINAL_DESCRIPTION,
  PAGE_TITLE as IB_TERMINAL_TITLE,
} from "./pages/interactive-brokers-dark-pool-terminal";
import {
  DATE_MODIFIED as UW_IB_LAST_MODIFIED,
  NAV_LABEL as UW_IB_NAV_LABEL,
  PAGE_DESCRIPTION as UW_IB_DESCRIPTION,
  PAGE_TITLE as UW_IB_TITLE,
  SLUG as UW_IB_SLUG,
} from "./pages/unusual-whales-interactive-brokers";

export type ClusterPage = {
  slug: string;
  navLabel: string;
  title: string;
  description: string;
  // Frozen ISO content date (never request-time), mirrored into the sitemap.
  lastModified: string;
};

export const clusterPages: ClusterPage[] = [
  {
    slug: IB_TERMINAL_SLUG,
    navLabel: IB_TERMINAL_NAV_LABEL,
    title: IB_TERMINAL_TITLE,
    description: IB_TERMINAL_DESCRIPTION,
    lastModified: IB_TERMINAL_LAST_MODIFIED,
  },
  {
    slug: CATALOG_SLUG,
    navLabel: CATALOG_NAV_LABEL,
    title: CATALOG_TITLE,
    description: CATALOG_DESCRIPTION,
    lastModified: CATALOG_LAST_MODIFIED,
  },
  {
    slug: KELLY_SLUG,
    navLabel: KELLY_NAV_LABEL,
    title: KELLY_TITLE,
    description: KELLY_DESCRIPTION,
    lastModified: KELLY_LAST_MODIFIED,
  },
  {
    slug: UW_IB_SLUG,
    navLabel: UW_IB_NAV_LABEL,
    title: UW_IB_TITLE,
    description: UW_IB_DESCRIPTION,
    lastModified: UW_IB_LAST_MODIFIED,
  },
  {
    slug: CONVEX_SLUG,
    navLabel: CONVEX_NAV_LABEL,
    title: CONVEX_TITLE,
    description: CONVEX_DESCRIPTION,
    lastModified: CONVEX_LAST_MODIFIED,
  },
  {
    slug: CRASH_RISK_INDEX_SLUG,
    navLabel: CRASH_RISK_INDEX_NAV_LABEL,
    title: CRASH_RISK_INDEX_TITLE,
    description: CRASH_RISK_INDEX_DESCRIPTION,
    lastModified: CRASH_RISK_INDEX_DATE_MODIFIED,
  },
];
