import type { ReactNode } from "react";
import { LegalDocument } from "@/components/sections/LegalDocument";
import {
  agentPageStructuredData,
  type AgentPage,
} from "@/lib/developer-pages";

export function AgentDocument({
  page,
  afterIntro,
}: {
  page: AgentPage;
  afterIntro?: ReactNode;
}) {
  return (
    <>
      <LegalDocument
        eyebrow={page.eyebrow}
        navLabel={page.navLabel}
        title={page.heading}
        effectiveDate={page.lastModified}
        dateLabel="Updated"
        intro={page.intro}
        sections={page.sections}
        afterIntro={afterIntro}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(agentPageStructuredData(page)),
        }}
      />
    </>
  );
}
