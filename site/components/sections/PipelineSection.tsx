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
          <p className="mt-5 text-secondary">
            The procedure is published in full, thresholds included, in{" "}
            <a
              href="/convex-options-from-dark-pool-flow"
              className="underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep"
            >
              the working method
            </a>
            , and the sizing milestone&apos;s math in{" "}
            <a
              href="/fractional-kelly-position-sizing"
              className="underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep"
            >
              the fractional Kelly sizing policy
            </a>
            .
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
