import type { Metadata } from "next";
import { LegalDocument } from "@/components/sections/LegalDocument";
import { LEGAL_EFFECTIVE_DATE } from "@/lib/legal";
import {
  TERMS_EYEBROW,
  TERMS_H1,
  TERMS_INTRO,
  TERMS_NAV_LABEL,
  termsMetadata,
  termsSections,
  termsStructuredData,
} from "@/lib/pages/terms";

export const metadata: Metadata = termsMetadata;

export default function TermsPage() {
  return (
    <>
      <LegalDocument
        eyebrow={TERMS_EYEBROW}
        navLabel={TERMS_NAV_LABEL}
        title={TERMS_H1}
        effectiveDate={LEGAL_EFFECTIVE_DATE}
        intro={TERMS_INTRO}
        sections={termsSections}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(termsStructuredData()),
        }}
      />
    </>
  );
}
