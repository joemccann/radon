// Content module for /terms: typed copy sections, per-page constants,
// metadata, and the BreadcrumbList builder (legal pages carry no TechArticle
// or FAQPage schema on purpose). Same single-source-of-truth pattern as the
// cluster page modules in lib/pages/, but /terms is a standalone legal route,
// never a clusterPages dossier.

import type { Metadata } from "next";
import {
  GOVERNING_LAW_REGION,
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

export const TERMS_SLUG = "terms";
export const TERMS_NAV_LABEL = "Terms of Service";
export const TERMS_TITLE = "Terms of Service | Radon Terminal";
export const TERMS_DESCRIPTION =
  "The terms that govern radon.run: research-only scope, no investment advice, no performance representations, limitation of liability, and governing law.";
export const TERMS_EYEBROW = "Legal · Terms of Service";
export const TERMS_H1 = "Terms of service";
export const TERMS_DATE_MODIFIED = LEGAL_EFFECTIVE_DATE;

export const termsUrl = `${siteUrl}/${TERMS_SLUG}`;

export const TERMS_INTRO =
  `These terms govern radon.run and the Radon Terminal content published on it, operated by ${LEGAL_ENTITY_NAME}. Read the investment disclaimers first: they are the point of the document.`;

export const termsSections: LegalSection[] = [
  {
    id: "agreement",
    heading: "Agreement to these terms",
    paragraphs: [
      `These terms of service are an agreement between you and ${LEGAL_ENTITY_NAME} ("Subprint", "we", "us"), the operator of Radon Terminal and this website, radon.run (together, the "service"). By accessing or using the service you agree to be bound by these terms. If you do not agree, do not use the service.`,
      "Other Radon properties, including the demo instance at demo.radon.run, are separate services governed by their own notices in addition to these terms.",
    ],
  },
  {
    id: "not-investment-advice",
    heading: "Not investment advice",
    paragraphs: [
      "Radon Terminal is a research instrument. Nothing on this site or in the software constitutes investment, financial, legal, or tax advice, an offer or solicitation to buy or sell any security or derivative, or a recommendation of any trading strategy. All content is provided for informational and educational purposes only.",
      `${LEGAL_ENTITY_NAME} is not a broker-dealer and is not registered as an investment adviser with the SEC, any state regulator, or any other authority, and makes no claim to such registration. No fiduciary relationship is created by your use of the service. Consult a qualified professional before making any investment decision.`,
    ],
  },
  {
    id: "no-performance-representations",
    heading: "No performance representations",
    paragraphs: [
      "Past performance does not predict future results. Backtests, exhibits, model outputs, signal reads, and journal figures shown on the service illustrate a method; they are not promises of outcome and are not indicative of results you should expect.",
      "Options trading involves substantial risk of loss and is not suitable for every investor. You are solely responsible for your trading and investment decisions and for any resulting gains or losses.",
    ],
  },
  {
    id: "acceptable-use",
    heading: "Acceptable use",
    paragraphs: [
      "You agree not to misuse the service. In particular, you will not attempt to gain unauthorized access to any system or account; probe, scan, or test the vulnerability of the service; scrape or harvest content at a volume that burdens the service; misrepresent the origin of the content; or use the service in violation of applicable law.",
    ],
  },
  {
    id: "intellectual-property",
    heading: "Intellectual property",
    paragraphs: [
      `The service, including its text, design, exhibits, models, and branding, is owned by ${LEGAL_ENTITY_NAME} or its licensors and is protected by intellectual property laws. Portions of the Radon source code are published at github.com/joemccann/radon; the license stated in that repository governs the code it covers.`,
      "Except as permitted by that license or by applicable law, you may not copy, modify, distribute, or create derivative works from the service without prior written consent. Quoting brief excerpts with attribution and a link to radon.run is welcome.",
    ],
  },
  {
    id: "third-party-services",
    heading: "Third-party services",
    paragraphs: [
      `The service references and links to independent third-party services, including Interactive Brokers and Unusual Whales. ${LEGAL_ENTITY_NAME} is not affiliated with, endorsed by, or responsible for those services. Their own terms and privacy policies govern your use of them, and any accounts, market data, or order executions you obtain through them are strictly between you and those providers.`,
      "Some links to Unusual Whales carry a referral code, as disclosed in the privacy policy.",
    ],
  },
  {
    id: "disclaimer-of-warranties",
    heading: "Disclaimer of warranties",
    paragraphs: [
      `The service is provided on an "as is" and "as available" basis, without warranties of any kind, express or implied, including warranties of merchantability, fitness for a particular purpose, title, non-infringement, accuracy, and uninterrupted or error-free operation. Market data and model outputs may be delayed, incomplete, or wrong. To the maximum extent permitted by law, ${LEGAL_ENTITY_NAME} disclaims all such warranties.`,
    ],
  },
  {
    id: "limitation-of-liability",
    heading: "Limitation of liability",
    paragraphs: [
      `To the maximum extent permitted by law, ${LEGAL_ENTITY_NAME} and its members, officers, and contributors will not be liable for any indirect, incidental, special, consequential, exemplary, or punitive damages, or for lost profits, lost data, or trading losses, arising out of or relating to the service, even if advised of the possibility of such damages.`,
      `To the same extent, the aggregate liability of ${LEGAL_ENTITY_NAME} for all claims relating to the service is capped at the greater of one hundred United States dollars ($100) or the amounts you paid ${LEGAL_ENTITY_NAME} for the service in the twelve months before the claim arose.`,
    ],
  },
  {
    id: "indemnification",
    heading: "Indemnification",
    paragraphs: [
      `You agree to indemnify and hold harmless ${LEGAL_ENTITY_NAME} and its members, officers, and contributors from and against any claims, damages, liabilities, and expenses, including reasonable attorneys' fees, arising from your use of the service or your violation of these terms.`,
    ],
  },
  {
    id: "termination",
    heading: "Termination",
    paragraphs: [
      "We may suspend or terminate access to the service at any time, with or without notice, for any reason, including violation of these terms. Sections that by their nature should survive termination, including the disclaimers, the limitation of liability, and the indemnification, survive.",
    ],
  },
  {
    id: "governing-law",
    heading: "Governing law",
    paragraphs: [
      `These terms are governed by the laws of ${GOVERNING_LAW_REGION}, without regard to its conflict of laws rules. Any dispute arising out of or relating to these terms or the service will be brought exclusively in the state or federal courts located there, and you consent to their jurisdiction.`,
    ],
  },
  {
    id: "changes",
    heading: "Changes to these terms",
    paragraphs: [
      "We may revise these terms from time to time. The effective date above reflects the latest revision, and material changes will be posted on this page. Continued use of the service after a revision takes effect constitutes acceptance of the revised terms.",
    ],
  },
  {
    id: "contact",
    heading: "Contact",
    paragraphs: [
      `Questions about these terms: ${LEGAL_CONTACT_EMAIL}.`,
    ],
  },
];

// --- Metadata ---------------------------------------------------------------
// Next metadata merge is shallow: defining openGraph here replaces the layout
// object, so the shared social image must be re-included from lib/seo.ts.

export const termsMetadata: Metadata = {
  title: TERMS_TITLE,
  description: TERMS_DESCRIPTION,
  alternates: {
    canonical: `/${TERMS_SLUG}`,
  },
  openGraph: {
    type: "website",
    url: `/${TERMS_SLUG}`,
    title: TERMS_TITLE,
    description: TERMS_DESCRIPTION,
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
    title: TERMS_TITLE,
    description: TERMS_DESCRIPTION,
    images: [SOCIAL_IMAGE_PATH],
  },
};

// --- JSON-LD ----------------------------------------------------------------
// BreadcrumbList only: legal pages are not articles and carry no TechArticle
// or FAQPage entities.

export function termsStructuredData() {
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
          name: TERMS_NAV_LABEL,
          item: termsUrl,
        },
      ],
    },
  ];
}
