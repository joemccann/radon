import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { RegistryTable } from "@/components/organisms/RegistryTable";

export function RegistrySection() {
  return (
    <section id="registry" className="px-8 py-[clamp(64px,9vw,128px)]">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="Registry" label="Named Edges · Mechanism & Risk Profile" />

        <RevealOnScroll className="mb-9 max-w-[66ch]">
          <h2 className="editorial-thesis mb-5 text-primary">
            Named options strategies, not a black box.
          </h2>
          <p className="text-secondary">
            Every strategy is a named, testable mechanism. Each entry states the edge
            source, the mechanism it exploits, and the structure&apos;s risk profile.
            Convexity is recorded as a target ratio.
          </p>
        </RevealOnScroll>

        <RevealOnScroll>
          <RegistryTable />
        </RevealOnScroll>
      </div>
    </section>
  );
}
