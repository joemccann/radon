// Shared constants for the legal pages (/privacy and /terms). Both documents
// identify the same operating entity, contact channel, and effective date so
// the facts live in exactly one place.

export const LEGAL_ENTITY_NAME = "Subprint Media, LLC";

// Confirmed by the operator 2026-07-04: Subprint Media, LLC is a Texas entity.
export const GOVERNING_LAW_REGION = "the State of Texas";
export const LEGAL_ADDRESS_COUNTRY = "US";
export const LEGAL_ADDRESS_REGION = "TX";

// Confirmed by the operator 2026-07-04, but the MAILBOX DOES NOT EXIST YET
// (Google Workspace setup pending). Until it does, privacy/terms point at a
// dead address; treat wiring hello@radon.run as an open operator task.
export const LEGAL_CONTACT_EMAIL = "hello@radon.run";

// Frozen ISO date (never request-time), rendered on both documents and
// mirrored into the sitemap lastmod.
export const LEGAL_EFFECTIVE_DATE = "2026-07-04";

export type LegalSection = {
  id: string;
  heading: string;
  paragraphs: string[];
};
