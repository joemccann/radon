import dynamic from "next/dynamic";
import { FooterSection } from "@/components/sections/FooterSection";
import { HeaderShell } from "@/components/sections/HeaderShell";
import { HeroSection } from "@/components/sections/HeroSection";

const StrategySection = dynamic(() =>
  import("@/components/sections/StrategySection").then((m) => m.StrategySection)
);
const ExecutionSection = dynamic(() =>
  import("@/components/sections/ExecutionSection").then((m) => m.ExecutionSection)
);
const PreviewSection = dynamic(() =>
  import("@/components/sections/PreviewSection").then((m) => m.PreviewSection)
);
const AuditSection = dynamic(() =>
  import("@/components/sections/AuditSection").then((m) => m.AuditSection)
);
const FinalCTASection = dynamic(() =>
  import("@/components/sections/FinalCTASection").then((m) => m.FinalCTASection)
);

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-canvas text-primary selection:bg-accent selection:text-canvas">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-4 focus:left-4 focus:z-50 focus:rounded focus:bg-accent focus:px-4 focus:py-2 focus:text-canvas focus:outline-none"
      >
        Skip to content
      </a>
      <div className="pointer-events-none fixed inset-0 z-0 instrument-grid opacity-[0.05]" />
      <div className="pointer-events-none fixed inset-0 z-10 projection-lines opacity-[0.08]" />
      <HeaderShell />
      <main id="main-content" className="relative z-20">
        <div className="mx-auto w-full max-w-[1440px] px-4 pb-14 pt-24 sm:px-6 lg:px-8">
          <HeroSection />
          <StrategySection />
          <ExecutionSection />
          <PreviewSection />
          <AuditSection />
          <FinalCTASection />
          <FooterSection />
        </div>
      </main>
    </div>
  );
}
