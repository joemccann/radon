import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { RegistryTable } from "@/components/organisms/RegistryTable";

export function RegistrySection() {
  return (
    <section id="registry" className="px-8 py-[clamp(64px,9vw,128px)]">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="Registry" label="Named edges" />

        <RevealOnScroll className="mb-9 max-w-[66ch]">
          <h2 className="editorial-thesis mb-5 text-primary">
            Each edge has a name and a structure.
          </h2>
          <p className="text-secondary">
            Each row lists the edge, how it pays, the structure, and the
            convexity target.
          </p>
        </RevealOnScroll>

        <RevealOnScroll>
          <RegistryTable />
        </RevealOnScroll>
      </div>
    </section>
  );
}
