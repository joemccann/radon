import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { MilestoneRow } from "@/components/molecules/MilestoneRow";
import { milestones } from "@/lib/editorial-content";

export function PipelineSection() {
  return (
    <section id="pipeline" className="px-8 py-[clamp(64px,9vw,128px)]">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="Method" label="The Pipeline · Seven Milestones" />

        <RevealOnScroll className="mb-10 max-w-[66ch]">
          <h2 className="editorial-thesis mb-5 text-primary">
            From dark-pool signal to routed order: seven milestones.
          </h2>
          <p className="text-[1.32rem] leading-[1.5] text-primary">
            A candidate is not a trade. Between the first flow score and a routed contract
            sit seven milestones, run in order. Each is a checkpoint with a pass condition.
            Nothing skips ahead.
          </p>
        </RevealOnScroll>

        <RevealOnScroll className="pipeline">
          {milestones.map((milestone) => (
            <MilestoneRow key={milestone.name} milestone={milestone} />
          ))}
        </RevealOnScroll>
      </div>
    </section>
  );
}
