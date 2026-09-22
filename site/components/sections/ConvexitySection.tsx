import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { CopyAgentPromptBar } from "@/components/molecules/CopyAgentPromptBar";
import { GateCard } from "@/components/molecules/GateCard";
import { gates } from "@/lib/editorial-content";

export function ConvexitySection() {
  return (
    <section id="discipline" className="px-5 py-[clamp(64px,9vw,128px)] sm:px-8">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="Discipline" label="The order still has to clear" />

        <RevealOnScroll className="mb-[42px] max-w-[66ch]">
          <h2 className="editorial-pull">
            A good view still dies at a bad structure.
          </h2>
          <p className="mt-6 text-secondary">
            Gates sit after the view and the vehicle. Fail one and the order
            stops. Conviction does not get a veto. Every structure that gets
            through is in{" "}
            <a
              href="/defined-risk-options-structures"
              className="underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep"
            >
              the defined-risk structure catalog
            </a>
            .
          </p>
          <CopyAgentPromptBar capabilityId="gates" />
        </RevealOnScroll>

        <RevealOnScroll className="gates">
          {gates.map((gate, index) => (
            <GateCard
              key={gate.no}
              gate={gate}
              capabilityId={`gate-0${index + 1}`}
            />
          ))}
        </RevealOnScroll>
      </div>
    </section>
  );
}
