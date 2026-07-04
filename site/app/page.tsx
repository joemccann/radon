import { ScrollProgress } from "@/components/atoms/ScrollProgress";
import { SectionRule } from "@/components/atoms/SectionRule";
import { ColophonSection } from "@/components/sections/ColophonSection";
import { ConvexitySection } from "@/components/sections/ConvexitySection";
import { EditorialFooter } from "@/components/sections/EditorialFooter";
import { EditorialHeader } from "@/components/sections/EditorialHeader";
import { EditorialHeroSection } from "@/components/sections/EditorialHeroSection";
import { EvidenceSection } from "@/components/sections/EvidenceSection";
import { FaqSection } from "@/components/sections/FaqSection";
import { FlowThesisSection } from "@/components/sections/FlowThesisSection";
import { PipelineSection } from "@/components/sections/PipelineSection";
import { RegimeSection } from "@/components/sections/RegimeSection";
import { RegistrySection } from "@/components/sections/RegistrySection";

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-canvas font-serif text-[19px] leading-[1.62] text-primary">
      <ScrollProgress />
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded focus:bg-accent focus:px-4 focus:py-2 focus:text-canvas focus:outline-none"
      >
        Skip to content
      </a>
      <EditorialHeader />
      <main id="main-content">
        <EditorialHeroSection />
        <SectionRule />
        <FlowThesisSection />
        <SectionRule />
        <ConvexitySection />
        <SectionRule />
        <RegimeSection />
        <SectionRule />
        <PipelineSection />
        <SectionRule />
        <RegistrySection />
        <SectionRule />
        <EvidenceSection />
        <SectionRule />
        <FaqSection />
        <ColophonSection />
        <EditorialFooter />
      </main>
    </div>
  );
}
