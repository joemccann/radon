import type { ReactNode } from "react";
import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { DEMO_URL } from "@/lib/editorial-content";
import { faqEntries } from "@/lib/faq-content";

const DEMO_HOST = "demo.radon.run";

// Linkifies demo.radon.run mentions on-page only; the FAQPage JSON-LD keeps
// the plain-text answer from faq-content.ts.
function linkifyDemoMentions(answer: string): ReactNode[] {
  return answer.split(DEMO_HOST).flatMap((segment, index) =>
    index === 0
      ? [segment]
      : [
          <a
            key={`demo-${index}`}
            href={DEMO_URL}
            className="underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep"
          >
            {DEMO_HOST}
          </a>,
          segment,
        ],
  );
}

export function FaqSection() {
  return (
    <section id="faq" className="px-8 py-[clamp(64px,9vw,128px)]">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="Appendix" label="Questions · Plain Answers" />

        <RevealOnScroll className="mb-[42px] max-w-[66ch]">
          <h2 className="editorial-thesis mb-[22px] text-primary">
            Asked before the demo.
          </h2>
          <p className="text-secondary">
            The questions traders bring to Radon, answered plainly. Data
            sources, broker requirements, and how this instrument differs from
            a levels dashboard.
          </p>
        </RevealOnScroll>

        <RevealOnScroll>
          <div className="border-t border-hairline-soft">
            {faqEntries.map((entry) => (
              <div
                key={entry.question}
                className="border-b border-hairline-soft py-[26px]"
              >
                <h3 className="mb-[7px] font-serif text-[1.32rem] font-medium tracking-[-0.01em] text-primary">
                  {entry.question}
                </h3>
                <p className="m-0 max-w-[66ch] text-[1.02rem] leading-[1.55] text-secondary">
                  {linkifyDemoMentions(entry.answer)}
                </p>
              </div>
            ))}
          </div>
        </RevealOnScroll>
      </div>
    </section>
  );
}
