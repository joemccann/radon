// Shared constants for the legal pages (/privacy and /terms). Both documents
// identify the same operating entity, contact channel, and effective date so
// the facts live in exactly one place.

export const LEGAL_ENTITY_NAME = "Subprint Media, LLC";

// FLAG (operator must confirm): no registration state for Subprint Media, LLC
// exists anywhere in the repo. Delaware is the conventional LLC default, not
// a verified fact. Update this constant if the LLC is organized elsewhere.
export const GOVERNING_LAW_REGION = "the State of Delaware";

// FLAG (operator must confirm): no contact address exists anywhere in the
// repo or site source. hello@radon.run is a conventional default.
export const LEGAL_CONTACT_EMAIL = "hello@radon.run";

// Frozen ISO date (never request-time), rendered on both documents and
// mirrored into the sitemap lastmod.
export const LEGAL_EFFECTIVE_DATE = "2026-07-04";

export type LegalSection = {
  id: string;
  heading: string;
  paragraphs: string[];
};
