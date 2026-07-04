import { EditorialEyebrow } from "@/components/atoms/EditorialEyebrow";
import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { PlateFrame } from "@/components/molecules/PlateFrame";
import { ProductPlate } from "@/components/molecules/ProductPlate";
import { FlowLeadFigure } from "@/components/organisms/FlowLeadFigure";
import { IB_URL, UW_URL } from "@/lib/editorial-content";

const sourceLink =
  "underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep";

export function EditorialHeroSection() {
  return (
    <section
      id="top"
      className="relative px-8 pb-[clamp(48px,6vw,80px)] pt-[clamp(56px,7vw,96px)]"
    >
      <svg
        className="geo-bg"
        aria-hidden="true"
        preserveAspectRatio="none"
        viewBox="0 0 1200 600"
      >
        <line x1="0" y1="120" x2="1200" y2="120" />
        <line x1="0" y1="240" x2="1200" y2="240" />
        <line x1="0" y1="360" x2="1200" y2="360" />
        <line x1="0" y1="480" x2="1200" y2="480" />
        <line x1="300" y1="0" x2="300" y2="600" />
        <line x1="600" y1="0" x2="600" y2="600" />
        <line x1="900" y1="0" x2="900" y2="600" />
      </svg>

      <div className="relative z-10 mx-auto max-w-[1140px]">
        <RevealOnScroll className="mb-[26px]">
          <EditorialEyebrow>Market-Structure Reconstruction</EditorialEyebrow>
        </RevealOnScroll>

        <RevealOnScroll as="h1" className="editorial-display mb-7 max-w-[15ch] text-primary">
          Institutions finish buying <em>before</em> the chart moves.
        </RevealOnScroll>

        <RevealOnScroll
          as="p"
          className="max-w-[52ch] text-[1.22rem] leading-[1.52] text-secondary"
        >
          Radon reconstructs market structure from dark-pool and OTC flow, then scores
          institutional accumulation while the lit price is still quiet. The read comes
          from{" "}
          <a href={IB_URL} target="_blank" rel="noopener noreferrer" className={sourceLink}>
            Interactive Brokers
          </a>{" "}
          and{" "}
          <a href={UW_URL} target="_blank" rel="noopener noreferrer" className={sourceLink}>
            Unusual Whales
          </a>{" "}
          data, not a model that guesses.
          Nothing routes until it clears four sequential gates and seven milestones:
          convexity, defined risk, Kelly size. No black box. Every edge is a named,
          testable mechanism.
        </RevealOnScroll>

        <RevealOnScroll className="mt-[34px] flex flex-wrap gap-7 font-mono text-[12px] tracking-[0.03em] text-muted">
          <span>
            Inputs{" "}
            <a href={IB_URL} target="_blank" rel="noopener noreferrer" className={sourceLink}>
              <b className="font-medium text-primary">Interactive Brokers</b>
            </a>{" "}
            realtime
          </span>
          <span>
            <a href={UW_URL} target="_blank" rel="noopener noreferrer" className={sourceLink}>
              <b className="font-medium text-primary">Unusual Whales</b>
            </a>{" "}
            dark-pool prints
          </span>
          <span>
            Regime read across <b className="font-medium text-primary">four</b> orthogonal
            models
          </span>
        </RevealOnScroll>

        <RevealOnScroll className="mt-14">
          <PlateFrame
            figNo="Figure 1"
            figTitle="Dark-pool buy-pressure leads realized price · NVDA · 30 sessions"
            source="IB + UW"
            confidence="High"
            caption="Aggregated off-exchange buy-pressure rises into a measurable lead before the lit-market move. Radon scores the lead window, not the breakout. The breakout is where everyone else is already looking."
          >
            <FlowLeadFigure />
          </PlateFrame>
        </RevealOnScroll>

        <RevealOnScroll className="mt-10">
          <ProductPlate
            figNo="Plate 2"
            figTitle="The instrument · unified dashboard cockpit · live session"
            shot="dashboard"
            lightAlt="Radon dashboard: portfolio snapshot, working orders, an opportunity rail, and a market newsfeed in a single cockpit."
            darkAlt="Radon dashboard, dark theme: portfolio snapshot, working orders, opportunity rail, and newsfeed."
            caption="The terminal itself. A single cockpit holds the portfolio snapshot, working orders, an opportunity rail scored from flow, and the market newsfeed. The conceptual figure above is the thesis; this is the instrument that runs it."
          />
        </RevealOnScroll>
      </div>
    </section>
  );
}
