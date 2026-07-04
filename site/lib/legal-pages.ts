// Registry for the standalone legal routes (/privacy, /terms). Deliberately
// separate from lib/cluster-pages.ts: legal pages are not dossiers, never
// appear in the homepage Dossiers index, and enter the sitemap at a lower
// priority. Each entry re-exports the page's own frozen constants so the
// content module stays the single source of truth.

import {
  PRIVACY_DATE_MODIFIED,
  PRIVACY_DESCRIPTION,
  PRIVACY_NAV_LABEL,
  PRIVACY_SLUG,
  PRIVACY_TITLE,
} from "./pages/privacy";
import {
  TERMS_DATE_MODIFIED,
  TERMS_DESCRIPTION,
  TERMS_NAV_LABEL,
  TERMS_SLUG,
  TERMS_TITLE,
} from "./pages/terms";

export type LegalPage = {
  slug: string;
  navLabel: string;
  title: string;
  description: string;
  // Frozen ISO content date (never request-time), mirrored into the sitemap.
  lastModified: string;
};

export const legalPages: LegalPage[] = [
  {
    slug: PRIVACY_SLUG,
    navLabel: PRIVACY_NAV_LABEL,
    title: PRIVACY_TITLE,
    description: PRIVACY_DESCRIPTION,
    lastModified: PRIVACY_DATE_MODIFIED,
  },
  {
    slug: TERMS_SLUG,
    navLabel: TERMS_NAV_LABEL,
    title: TERMS_TITLE,
    description: TERMS_DESCRIPTION,
    lastModified: TERMS_DATE_MODIFIED,
  },
];
