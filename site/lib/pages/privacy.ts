// Content module for /privacy: typed copy sections, per-page constants,
// metadata, and the BreadcrumbList builder (legal pages carry no TechArticle
// or FAQPage schema on purpose). The disclosures here mirror the audited
// inventory exactly: one user-initiated localStorage key ("theme"), zero
// cookies, cookieless same-origin Vercel Web Analytics, standard Vercel
// hosting logs, and nothing else. lib/privacy-claims.test.ts pins the
// source-level facts so this copy breaks loudly if a tracker is ever added.

import type { Metadata } from "next";
import {
  LEGAL_CONTACT_EMAIL,
  LEGAL_EFFECTIVE_DATE,
  LEGAL_ENTITY_NAME,
  type LegalSection,
} from "../legal";
import {
  SITE_NAME,
  SOCIAL_IMAGE_ALT,
  SOCIAL_IMAGE_PATH,
  siteUrl,
} from "../seo";

export const PRIVACY_SLUG = "privacy";
export const PRIVACY_NAV_LABEL = "Privacy Policy";
export const PRIVACY_TITLE = "Privacy Policy | Radon Terminal";
export const PRIVACY_DESCRIPTION =
  "How radon.run handles data: a functional theme preference, cookieless first-party analytics, standard hosting logs, and no sale of personal information.";
export const PRIVACY_EYEBROW = "Legal · Privacy Policy";
export const PRIVACY_H1 = "Privacy policy";
export const PRIVACY_DATE_MODIFIED = LEGAL_EFFECTIVE_DATE;

export const privacyUrl = `${siteUrl}/${PRIVACY_SLUG}`;

export const PRIVACY_INTRO =
  "radon.run is a static research journal. It sets no cookies, shows no consent banner because none is warranted, and collects nothing you type, because there is nowhere to type it. This page is the complete inventory.";

export const privacySections: LegalSection[] = [
  {
    id: "scope",
    heading: "Who we are and what this policy covers",
    paragraphs: [
      `This policy explains how ${LEGAL_ENTITY_NAME} ("Subprint", "we", "us"), the operator of Radon Terminal, handles data on radon.run. ${LEGAL_ENTITY_NAME} is the data controller for the processing described here.`,
      "The policy is scoped to radon.run only. Other Radon properties, including the demo at demo.radon.run, are separate origins with their own processing, described in the section on other services below.",
    ],
  },
  {
    id: "what-we-collect",
    heading: "What happens when you visit",
    paragraphs: [
      "radon.run is a static marketing site: no forms, no sign-in, no comment box, and no code that sends what you read or click to us for profiling. The complete list of data pathways follows.",
      "Device storage: a single localStorage key named \"theme\", holding the value \"dark\" or \"light\". It is written only if you click the theme toggle, it stays on your device, and it is never transmitted to us.",
      "Hosting logs: the site is served by Vercel. Like any host, Vercel processes standard server request logs (IP address, user agent, requested URL, timestamp) to deliver and secure the site.",
      "Analytics: cookieless, first-party page-view counting, described in the analytics section below.",
      "That is the entire list. We collect no names, no email addresses, no account data, and no payment information on radon.run.",
    ],
  },
  {
    id: "cookies",
    heading: "Cookies and consent",
    paragraphs: [
      "radon.run sets no cookies, first-party or third-party. The only thing stored on your device is the theme preference described above, written only after you request it by clicking the toggle.",
      "We do not show a cookie consent banner because there is nothing to consent to: the theme key is a functional preference you initiate, which falls within the ePrivacy strictly-necessary and functional exemption; the analytics stores nothing on and reads nothing from your device; and no third-party trackers, advertising technology, or cross-site identifiers are present. If any of that ever changes, this policy and the consent mechanism will change first.",
    ],
  },
  {
    id: "analytics",
    heading: "Analytics",
    paragraphs: [
      "We use Vercel Web Analytics to count page views. The script is served from radon.run itself and sends page-view beacons (page URL, referrer, screen size, user agent) to the same origin. It sets no cookies and does not read or write your device storage.",
      "Vercel derives coarse location (country) from the IP address server-side and distinguishes repeat views using a hash of the incoming request that rotates daily and is then discarded. No persistent identifier of you is created or stored on your device, and no cross-site tracking takes place. What we see is aggregated counts, not a profile of you.",
    ],
  },
  {
    id: "legal-bases",
    heading: "Legal bases",
    paragraphs: [
      "Where the GDPR or UK GDPR applies, we rely on legitimate interest (Article 6(1)(f)) for the hosting logs and the cookieless analytics: operating, securing, and understanding aggregate use of the site. The theme preference is stored at your request on your device and is not processed by us at all.",
    ],
  },
  {
    id: "retention",
    heading: "Retention",
    paragraphs: [
      "The theme key stays on your device until you or your browser clears it. Hosting request logs and analytics data are retained by Vercel under its published retention practices, transient for raw logs and aggregated for analytics. We keep no separate copies.",
    ],
  },
  {
    id: "international-transfers",
    heading: "International transfers",
    paragraphs: [
      "The site is hosted by Vercel Inc., a United States company, so the log and analytics processing described above takes place in the United States. Where the GDPR or UK GDPR applies, those transfers rely on Vercel's safeguards, including standard contractual clauses.",
    ],
  },
  {
    id: "gdpr-rights",
    heading: "Your rights under the GDPR and UK GDPR",
    paragraphs: [
      "If you are in the European Economic Area or the United Kingdom, you have the rights of access, rectification, erasure, restriction, portability, and objection over personal data we control, and the right to lodge a complaint with your supervisory authority.",
      "In practice, radon.run holds almost nothing to exercise those rights against: we keep no account of you and cannot identify you within aggregated analytics. Contact us at the address below and we will do whatever the data allows.",
    ],
  },
  {
    id: "california-rights",
    heading: "California privacy rights",
    paragraphs: [
      "If you are a California resident, the CCPA as amended by the CPRA gives you the rights to know, delete, and correct personal information, and to opt out of its sale or sharing. We do not sell personal information and we do not share it for cross-context behavioral advertising, so there is nothing to opt out of. We do not use or disclose sensitive personal information, and we do not discriminate against anyone for exercising these rights.",
    ],
  },
  {
    id: "other-services",
    heading: "Other Radon services and outbound links",
    paragraphs: [
      `demo.radon.run is a separate service operated by ${LEGAL_ENTITY_NAME} on separate infrastructure. Creating a demo account there involves account data, such as your email address, processed through Clerk, the authentication provider for that service, under that service's own notices. app.radon.run is the operator's private instance and is not open for signup. This policy covers neither origin.`,
      "Outbound links lead to independent services with their own privacy practices: Interactive Brokers, Unusual Whales, GitHub, and X. The Unusual Whales link on this site carries a referral code: if you sign up there, the operator may receive referral credit, and Unusual Whales applies its own cookies and terms on its own site. radon.run sends nothing to any of these hosts when you merely load the page.",
    ],
  },
  {
    id: "children",
    heading: "Children",
    paragraphs: [
      "The service is financial research content and is not directed at anyone under 18. We do not knowingly collect personal information from minors, and there is no mechanism on radon.run to submit any.",
    ],
  },
  {
    id: "changes",
    heading: "Changes to this policy",
    paragraphs: [
      "We may update this policy as the site changes. The effective date above reflects the latest revision, and material changes will be posted on this page.",
    ],
  },
  {
    id: "contact",
    heading: "Contact",
    paragraphs: [
      `Privacy questions or requests: ${LEGAL_CONTACT_EMAIL}.`,
    ],
  },
];

// --- Metadata ---------------------------------------------------------------
// Next metadata merge is shallow: defining openGraph here replaces the layout
// object, so the shared social image must be re-included from lib/seo.ts.

export const privacyMetadata: Metadata = {
  title: PRIVACY_TITLE,
  description: PRIVACY_DESCRIPTION,
  alternates: {
    canonical: `/${PRIVACY_SLUG}`,
  },
  openGraph: {
    type: "website",
    url: `/${PRIVACY_SLUG}`,
    title: PRIVACY_TITLE,
    description: PRIVACY_DESCRIPTION,
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
    title: PRIVACY_TITLE,
    description: PRIVACY_DESCRIPTION,
    images: [SOCIAL_IMAGE_PATH],
  },
};

// --- JSON-LD ----------------------------------------------------------------
// BreadcrumbList only: legal pages are not articles and carry no TechArticle
// or FAQPage entities.

export function privacyStructuredData() {
  return [
    {
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
          name: PRIVACY_NAV_LABEL,
          item: privacyUrl,
        },
      ],
    },
  ];
}
