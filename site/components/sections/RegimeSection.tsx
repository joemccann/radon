import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { ProductPlate } from "@/components/molecules/ProductPlate";
import { RegimeModelCard } from "@/components/organisms/RegimeModelCard";
import { regimeModels } from "@/lib/editorial-content";

export function RegimeSection() {
  return (
    <section id="regime" className="px-8 py-[clamp(64px,9vw,128px)]">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="View" label="Four regime models" />

        <RevealOnScroll className="mb-10 max-w-[66ch]">
          <h2 className="editorial-thesis text-primary">
            One number will not cover the tape.
          </h2>
          <p className="mt-5 text-secondary">
            Four models, each watching a different pressure. If they agree, trust
            the read more. If they disagree, that split is the useful part.
          </p>
        </RevealOnScroll>

        <RevealOnScroll className="grid gap-6 md:grid-cols-2">
          {regimeModels.map((model) => (
            <RegimeModelCard key={model.code} model={model} />
          ))}
        </RevealOnScroll>

        <RevealOnScroll className="mt-8 max-w-[66ch]">
          <p className="text-secondary">
            How{" "}
            <a
              href="/crash-risk-index"
              className="underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep"
            >
              the Crash Risk Index
            </a>{" "}
            is scored from VIX, VVIX, implied correlation, and SPX trend,
            and how the bands are read, has its own note.
          </p>
        </RevealOnScroll>

        <RevealOnScroll className="mt-12 grid items-start gap-[clamp(36px,5vw,72px)] md:grid-cols-2">
          <ProductPlate
            figNo="Figure 4"
            figTitle="GEX · dealer gamma by strike · live session"
            shot="regime-gex"
            lightAlt="Radon GEX panel: dealer gamma exposure plotted by strike, with walls and magnets marked and a directional bias read."
            darkAlt="Radon GEX panel, dark theme: dealer gamma exposure by strike, with walls, magnets, and bias."
            caption="Dealer net gamma by strike. Walls and magnets are the levels used for targets."
          />
          <ProductPlate
            figNo="Figure 5"
            figTitle="VCG-R · volatility-credit gap · live session"
            shot="regime-vcg"
            lightAlt="Volatility-Credit Gap panic detector: the spread between equity-implied vol and credit-implied stress, with the gap and its direction."
            darkAlt="Volatility-Credit Gap panic detector, dark theme: equity-implied vol versus credit-implied stress."
            caption="When credit stress leads equity vol and the gap widens, the options market has not priced that panic yet."
          />
        </RevealOnScroll>
      </div>
    </section>
  );
}
