import { CtaBeam } from "@/components/atoms/CtaBeam";
import { EditorialEyebrow } from "@/components/atoms/EditorialEyebrow";
import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { DEMO_URL } from "@/lib/editorial-content";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-panel-raised";

export function ColophonSection() {
  return (
    <section
      id="cta"
      className="relative border-t border-grid bg-panel-raised px-8 py-[clamp(64px,9vw,128px)]"
    >
      <svg
        className="geo-bg"
        aria-hidden="true"
        preserveAspectRatio="none"
        viewBox="0 0 1200 400"
      >
        <line x1="0" y1="100" x2="1200" y2="100" />
        <line x1="0" y1="200" x2="1200" y2="200" />
        <line x1="0" y1="300" x2="1200" y2="300" />
        <line x1="400" y1="0" x2="400" y2="400" />
        <line x1="800" y1="0" x2="800" y2="400" />
      </svg>

      <div className="relative z-10 mx-auto max-w-[1140px]">
        <RevealOnScroll className="mb-[26px]">
          <EditorialEyebrow>Colophon</EditorialEyebrow>
        </RevealOnScroll>

        <RevealOnScroll
          as="h2"
          className="editorial-pull mb-[34px] max-w-[22ch] border-0 p-0"
        >
          For operators who want the method, not the noise.
        </RevealOnScroll>

        <RevealOnScroll as="p" className="mb-[34px] max-w-[66ch] text-secondary">
          Radon is built for a single operator who treats trading as research. If you read
          this far and nodded, you already understand the discipline. Convexity or no trade.
        </RevealOnScroll>

        <RevealOnScroll className="flex flex-wrap items-center gap-x-6 gap-y-4">
          <CtaBeam>
            <a
              href={DEMO_URL}
              className={`inline-block rounded-[4px] border border-signal-deep bg-signal-deep px-[22px] py-[13px] font-mono text-[12px] uppercase tracking-[0.06em] text-canvas transition-colors hover:bg-transparent hover:text-signal-deep ${focusRing}`}
            >
              Try the free demo
            </a>
          </CtaBeam>
          <a
            href="#pipeline"
            className={`inline-block rounded-[4px] border border-grid bg-transparent px-[22px] py-[13px] font-mono text-[12px] uppercase tracking-[0.06em] text-primary transition-colors hover:border-signal-deep hover:text-signal-deep ${focusRing}`}
          >
            Read the method
          </a>
          <a
            href="#registry"
            className={`font-mono text-[12px] uppercase tracking-[0.06em] text-secondary underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep ${focusRing}`}
          >
            Review the registry
          </a>
        </RevealOnScroll>
      </div>
    </section>
  );
}
