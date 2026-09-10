import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { CopyAgentPromptBar } from "@/components/molecules/CopyAgentPromptBar";
import { PlateFrame } from "@/components/molecules/PlateFrame";
import { ScannerTable } from "@/components/organisms/ScannerTable";
import { flowArgumentSteps } from "@/lib/editorial-content";

export function FlowThesisSection() {
  return (
    <section id="view" className="px-5 py-[clamp(64px,9vw,128px)] sm:px-8">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="View" label="How the view is built" />

        <div className="grid items-start gap-[clamp(36px,5vw,72px)] md:grid-cols-2">
          <RevealOnScroll>
            <h2 className="editorial-thesis mb-[22px] text-primary">
              Size on the exchange is already spoken for.
            </h2>
            <p className="mb-[1.05em] text-secondary">
              Big orders clear off-exchange. By the time that size hits the lit tape,
              the position is already on. Radon reads the print stream and scores it
              as accumulation or distribution before the chart moves.
            </p>
            <p className="mb-[1.05em] text-secondary">
              A score that misses its threshold is thrown out, even if the chart
              looks clean. The lead is a measured window per ticker, not a hunch.
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

            <CopyAgentPromptBar capabilityId="flow" className="mt-5" />

            <p className="mt-5 text-[1.02rem] leading-[1.55] text-secondary">
              Unusual Whales prints to Interactive Brokers orders, start to
              finish, is in{" "}
              <a
                href="/unusual-whales-interactive-brokers"
                className="underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep"
              >
                the integration dossier
              </a>
              .
            </p>
          </RevealOnScroll>

          <RevealOnScroll>
            <PlateFrame
              figNo="Figure 3"
              figTitle="Dark-pool flow scanner · institutional accumulation score"
              source="UW"
              confidence="High"
              caption="Above the accumulation line with a lead: that is a candidate. Rank is by lead and score, not raw volume."
            >
              <ScannerTable />
            </PlateFrame>
          </RevealOnScroll>
        </div>
      </div>
    </section>
  );
}
