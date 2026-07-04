import Link from "next/link";
import { EditorialEyebrow } from "@/components/atoms/EditorialEyebrow";
import { EditorialFooter } from "@/components/sections/EditorialFooter";
import { EditorialHeader } from "@/components/sections/EditorialHeader";
import type { LegalSection } from "@/lib/legal";

// Quieter Editorial variant for the legal routes: same header, footer, and
// hairline grammar as the dossiers, but a single reading column, a mono table
// of contents, and no reveal animation or scroll chrome (server-rendered,
// zero client JS of its own).

interface LegalDocumentProps {
  eyebrow: string;
  navLabel: string;
  title: string;
  effectiveDate: string;
  intro: string;
  sections: LegalSection[];
}

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";

function sectionNumber(index: number): string {
  return String(index + 1).padStart(2, "0");
}

function LegalBreadcrumb({ navLabel }: { navLabel: string }) {
  return (
    <nav aria-label="Breadcrumb" className="border-b border-hairline-soft">
      <div className="mx-auto flex max-w-[1140px] items-center gap-[10px] px-8 py-[13px] font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted">
        <Link
          href="/"
          className={`transition-colors hover:text-signal-deep ${focusRing}`}
        >
          Radon
        </Link>
        <span aria-hidden="true">/</span>
        <span className="text-signal-deep">{navLabel}</span>
      </div>
    </nav>
  );
}

export function LegalDocument({
  eyebrow,
  navLabel,
  title,
  effectiveDate,
  intro,
  sections,
}: LegalDocumentProps) {
  return (
    <div className="min-h-screen bg-canvas font-serif text-[17.5px] leading-[1.62] text-primary">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded focus:bg-accent focus:px-4 focus:py-2 focus:text-canvas focus:outline-none"
      >
        Skip to content
      </a>
      <EditorialHeader />
      <main id="main-content">
        <LegalBreadcrumb navLabel={navLabel} />

        <article className="px-8 pb-[clamp(56px,7vw,96px)] pt-[clamp(48px,6vw,80px)]">
          <div className="mx-auto max-w-[760px]">
            <div className="mb-[26px]">
              <EditorialEyebrow>{eyebrow}</EditorialEyebrow>
            </div>

            <h1 className="mb-5 max-w-[24ch] font-serif text-[clamp(2.2rem,4.6vw,3.2rem)] font-medium leading-[1.06] tracking-[-0.02em] text-primary">
              {title}
            </h1>

            <p className="mb-7 font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted">
              Effective {effectiveDate}
            </p>

            <p className="max-w-[66ch] text-[1.12rem] leading-[1.55] text-secondary">
              {intro}
            </p>

            <nav
              aria-label="Contents"
              className="mt-11 border-y border-hairline-soft py-[26px]"
            >
              <ol className="grid list-none gap-x-8 gap-y-[9px] font-mono text-[12px] tracking-[0.03em] sm:grid-cols-2">
                {sections.map((section, index) => (
                  <li key={section.id}>
                    <a
                      href={`#${section.id}`}
                      className={`text-secondary transition-colors hover:text-signal-deep ${focusRing}`}
                    >
                      <span className="mr-[10px] text-muted">
                        {sectionNumber(index)}
                      </span>
                      {section.heading}
                    </a>
                  </li>
                ))}
              </ol>
            </nav>

            {sections.map((section, index) => (
              <section
                key={section.id}
                id={section.id}
                className={`scroll-mt-[74px] py-[34px] ${
                  index === 0 ? "" : "border-t border-hairline-soft"
                }`}
              >
                <h2 className="mb-4 font-serif text-[1.32rem] font-medium tracking-[-0.01em] text-primary">
                  <span className="mr-3 font-mono text-[11.5px] uppercase tracking-[0.14em] text-signal-deep">
                    {sectionNumber(index)}
                  </span>
                  {section.heading}
                </h2>
                <div className="max-w-[66ch] space-y-4 text-secondary">
                  {section.paragraphs.map((paragraph) => (
                    <p key={paragraph}>{paragraph}</p>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </article>

        <EditorialFooter />
      </main>
    </div>
  );
}
