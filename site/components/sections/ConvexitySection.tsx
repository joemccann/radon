import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { GateCard } from "@/components/molecules/GateCard";
import { gates } from "@/lib/editorial-content";

export function ConvexitySection() {
  return (
    <section id="convexity" className="px-8 py-[clamp(64px,9vw,128px)]">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="Thesis 02" label="Convexity · The Four Gates" />

        <RevealOnScroll className="mb-[42px] max-w-[66ch]">
          <p className="editorial-pull">
            A signal is permission to look. Convexity is permission to trade.
          </p>
          <p className="mt-6 text-secondary">
            Every candidate is forced through four sequential gates before a contract is
            built. The gates are ordered. A failure at any gate stops the trade and names
            itself. There is no override that lets conviction outrank structure.
          </p>
        </RevealOnScroll>

        <RevealOnScroll className="gates">
          {gates.map((gate) => (
            <GateCard key={gate.no} gate={gate} />
          ))}
        </RevealOnScroll>
      </div>
    </section>
  );
}
