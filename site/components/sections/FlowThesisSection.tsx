import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { PlateFrame } from "@/components/molecules/PlateFrame";
import { ScannerTable } from "@/components/organisms/ScannerTable";
import { flowArgumentSteps } from "@/lib/editorial-content";

export function FlowThesisSection() {
  return (
    <section id="flow" className="px-8 py-[clamp(64px,9vw,128px)]">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="Thesis 01" label="Flow · Dark-Pool Reconstruction" />

        <div className="grid items-start gap-[clamp(36px,5vw,72px)] md:grid-cols-2">
          <RevealOnScroll>
            <h2 className="editorial-thesis mb-[22px] text-primary">
              The edge is structural, not predictive.
            </h2>
            <p className="mb-[1.05em] text-secondary">
              Off-exchange venues clear large institutional orders away from the lit tape.
              By the time size prints on the exchange, the position is built. Radon
              reconstructs that activity from the print stream and scores it as
              accumulation or distribution before the move appears on a chart.
            </p>
            <p className="mb-[1.05em] text-secondary">
              The lead is measured, not assumed. Accumulation shows up in the print
              stream sessions before it shows up in price, and a score that misses its
              threshold is discarded, whatever the chart looks like.
            </p>

            <div className="mt-7 border-t border-hairline-soft">
              {flowArgumentSteps.map((step) => (
                <div
                  key={step.stage}
                  className="grid grid-cols-[auto_1fr] gap-4 border-b border-hairline-soft py-4"
                >
                  <span className="whitespace-nowrap pt-1 font-mono text-[10.5px] uppercase tracking-[0.12em] text-signal-deep">
                    {step.stage}
                  </span>
                  <p className="m-0 text-[1.02rem] leading-[1.5] text-secondary">
                    {step.body}
                  </p>
                </div>
              ))}
            </div>
          </RevealOnScroll>

          <RevealOnScroll>
            <PlateFrame
              figNo="Exhibit 3"
              figTitle="Dark-pool flow scanner · institutional accumulation score"
              source="UW"
              confidence="High"
              caption="Scores above the accumulation threshold with a positive lead are candidates. The scanner ranks by lead-adjusted conviction, not raw volume."
            >
              <ScannerTable />
            </PlateFrame>
          </RevealOnScroll>
        </div>
      </div>
    </section>
  );
}
