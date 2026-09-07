import { EditorialEyebrow } from "@/components/atoms/EditorialEyebrow";
import { HeroBeam } from "@/components/atoms/HeroBeam";
import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { PlateFrame } from "@/components/molecules/PlateFrame";
import { ProductPlate } from "@/components/molecules/ProductPlate";
import { FlowLeadFigure } from "@/components/organisms/FlowLeadFigure";
import { SystemsLoop } from "@/components/organisms/SystemsLoop";

export function EditorialHeroSection() {
  return (
    <section
      id="top"
      className="relative px-5 pb-[clamp(48px,6vw,80px)] pt-[clamp(48px,6vw,88px)] sm:px-8"
    >
      <div className="relative z-10 mx-auto max-w-[1140px]">
        <RevealOnScroll initiallyShown className="mb-6">
          <EditorialEyebrow>The lead, then the position</EditorialEyebrow>
        </RevealOnScroll>

        <RevealOnScroll
          initiallyShown
          as="h1"
          className="editorial-display mb-8 max-w-3xl text-primary"
        >
          Institutions finish buying <em>before</em> the chart moves.
        </RevealOnScroll>

        <RevealOnScroll
          initiallyShown
          as="p"
          className="max-w-[54ch] text-[1.125rem] leading-[1.55] text-secondary"
        >
          Off-exchange size builds while the lit tape is quiet. Radon scores that
          lead, then sizes a defined-risk way to own it.
        </RevealOnScroll>

        <RevealOnScroll initiallyShown className="mt-10">
          <SystemsLoop />
        </RevealOnScroll>

        <RevealOnScroll className="mt-14">
          <HeroBeam>
            <PlateFrame
              figNo="Figure 1"
              figTitle="Dark-pool buy-pressure leads realized price · NVDA · 30 sessions"
              source="IB + UW"
              confidence="High"
              caption="Off-exchange buy pressure rises before the lit move. The structure comes after."
            >
              <FlowLeadFigure />
            </PlateFrame>
          </HeroBeam>
        </RevealOnScroll>

        <RevealOnScroll className="mt-10">
          <ProductPlate
            figNo="Figure 2"
            figTitle="Positions · demo book"
            shot="portfolio"
            lightAlt="Radon Positions screen with a made-up book: bankroll, day P&L, and four defined-risk rows."
            darkAlt="Radon Positions screen in dark theme with a made-up book and four defined-risk rows."
            caption="These rows are invented. The screen is the real desk."
            source="Demo book"
            confidence="Not a live account"
          />
        </RevealOnScroll>
      </div>
    </section>
  );
}
