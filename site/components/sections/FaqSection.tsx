import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { faqEntries } from "@/lib/faq-content";

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
                  {entry.answer}
                </p>
              </div>
            ))}
          </div>
        </RevealOnScroll>
      </div>
    </section>
  );
}
