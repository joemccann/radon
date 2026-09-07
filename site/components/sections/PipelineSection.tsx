import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { MilestoneRow } from "@/components/molecules/MilestoneRow";
import { milestones } from "@/lib/editorial-content";

export function PipelineSection() {
  return (
    <section id="expression" className="px-5 py-[clamp(64px,9vw,128px)] sm:px-8">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="Expression" label="Choose the vehicle, then the structure" />

        <RevealOnScroll className="mb-10 max-w-[66ch]">
          <h2 className="editorial-thesis mb-5 text-primary">
            The same view can be a spread, a stock, or a future.
          </h2>
          <p className="text-[1.2rem] leading-[1.5] text-primary">
            Once the view is named, pick the cheapest way to own it. Defined-risk
            options first. Stock or futures if they are cleaner. Seven steps, in
            order.
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
