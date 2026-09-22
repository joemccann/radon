import Link from "next/link";
import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { DEMO_URL } from "@/lib/editorial-content";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";

interface Props {
  body: string;
  secondaryHref: string;
  secondaryLabel: string;
}

export function ClusterCta({ body, secondaryHref, secondaryLabel }: Props) {
  return (
    <section id="cta" className="px-5 py-[clamp(64px,9vw,128px)] sm:px-8">
      <div className="mx-auto max-w-[1140px]">
        <RevealOnScroll>
          <h2 className="editorial-thesis mb-5 text-primary">
            Open the demo.
          </h2>
          <p className="mb-9 max-w-[66ch] text-secondary">{body}</p>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-4">
            <a
              href={DEMO_URL}
              className={`inline-flex min-h-11 items-center rounded-[8px] border border-signal-deep bg-signal-deep px-[22px] font-sans text-[13px] font-medium text-canvas transition-colors hover:bg-transparent hover:text-signal-deep ${focusRing}`}
            >
              Try the free demo
            </a>
            <Link
              href={secondaryHref}
              className={`inline-flex min-h-11 items-center font-sans text-[14px] text-secondary underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep ${focusRing}`}
            >
              {secondaryLabel}
            </Link>
          </div>
        </RevealOnScroll>
      </div>
    </section>
  );
}
