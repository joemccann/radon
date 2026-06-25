import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { ProductPlate } from "@/components/molecules/ProductPlate";
import { RegimeModelCard } from "@/components/organisms/RegimeModelCard";
import { regimeModels } from "@/lib/editorial-content";

export function RegimeSection() {
  return (
    <section id="regime" className="px-8 py-[clamp(64px,9vw,128px)]">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="Thesis 03" label="Regime · Four Orthogonal Models" />

        <RevealOnScroll className="mb-10 max-w-[66ch]">
          <h2 className="editorial-thesis text-primary">
            No single number describes a market.
          </h2>
          <p className="mt-5 text-secondary">
            Regime is read across four independent models, each measuring a different
            mechanical pressure. They are deliberately orthogonal. When they agree, the
            read is strong. When they diverge, the divergence is itself the signal.
          </p>
        </RevealOnScroll>

        <RevealOnScroll className="grid gap-6 md:grid-cols-2">
          {regimeModels.map((model) => (
            <RegimeModelCard key={model.code} model={model} />
          ))}
        </RevealOnScroll>

        <RevealOnScroll className="mt-12 grid items-start gap-[clamp(36px,5vw,72px)] md:grid-cols-2">
          <ProductPlate
            figNo="Plate 4"
            figTitle="GEX · dealer gamma by strike · live session"
            shot="regime-gex"
            lightAlt="Gamma Exposure panel: dealer net gamma plotted by strike, with walls and magnets marked and a directional bias read."
            darkAlt="Gamma Exposure panel, dark theme: dealer net gamma by strike, with walls, magnets, and bias."
            caption="The Gamma Exposure model rendered as a real exhibit. Dealer net gamma by strike resolves into the walls and magnets that the method note describes as price levels."
          />
          <ProductPlate
            figNo="Plate 5"
            figTitle="VCG-R · volatility-credit gap · live session"
            shot="regime-vcg"
            lightAlt="Volatility-Credit Gap panic detector: the spread between equity-implied vol and credit-implied stress, with the gap and its direction."
            darkAlt="Volatility-Credit Gap panic detector, dark theme: equity-implied vol versus credit-implied stress."
            caption="The panic detector in production. When credit-implied stress leads equity vol and the gap widens, the model flags capitulation the options market has not yet priced."
          />
        </RevealOnScroll>
      </div>
    </section>
  );
}
