import type { Metadata } from "next";
import { LegalDocument } from "@/components/sections/LegalDocument";
import { LEGAL_EFFECTIVE_DATE } from "@/lib/legal";
import {
  PRIVACY_EYEBROW,
  PRIVACY_H1,
  PRIVACY_INTRO,
  PRIVACY_NAV_LABEL,
  privacyMetadata,
  privacySections,
  privacyStructuredData,
} from "@/lib/pages/privacy";

export const metadata: Metadata = privacyMetadata;

export default function PrivacyPage() {
  return (
    <>
      <LegalDocument
        eyebrow={PRIVACY_EYEBROW}
        navLabel={PRIVACY_NAV_LABEL}
        title={PRIVACY_H1}
        effectiveDate={LEGAL_EFFECTIVE_DATE}
        intro={PRIVACY_INTRO}
        sections={privacySections}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(privacyStructuredData()),
        }}
      />
    </>
  );
}
