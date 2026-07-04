import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { clusterPages } from "@/lib/cluster-pages";

// The primary crawl path from the homepage into the cluster pages: one ruled
// index row per dossier, rendered like the registry (mono label, serif
// summary, link). Rows come from the cluster registry in lib/cluster-pages.ts.
export function DossiersSection() {
  return (
    <section id="dossiers" className="px-8 py-[clamp(64px,9vw,128px)]">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="Index" label="Dossiers · The Method In Depth" />

        <RevealOnScroll className="mb-9 max-w-[66ch]">
          <h2 className="editorial-thesis mb-5 text-primary">
            Every claim above has a longer argument.
          </h2>
          <p className="text-secondary">
            Six dossiers extend this page: the terminal on Interactive Brokers
            rails, the structure catalog, the sizing policy, the data wiring,
            the working method, and the crash-risk regime model. Each states
            its own scope and links back here.
          </p>
        </RevealOnScroll>

        <RevealOnScroll>
          <div className="border-t border-hairline-soft">
            {clusterPages.map((page) => (
              <a
                key={page.slug}
                href={`/${page.slug}`}
                className="group grid grid-cols-1 gap-x-6 gap-y-2 border-b border-hairline-soft py-[22px] md:grid-cols-[260px_1fr_auto]"
              >
                <span className="font-mono text-[11.5px] uppercase tracking-[0.12em] text-signal-deep">
                  {page.navLabel}
                </span>
                <p className="m-0 max-w-[66ch] text-[1.02rem] leading-[1.55] text-secondary transition-colors group-hover:text-primary">
                  {page.description}
                </p>
                <span
                  aria-hidden="true"
                  className="hidden self-center font-mono text-[12px] text-muted transition-colors group-hover:text-signal-deep md:inline"
                >
                  &rarr;
                </span>
              </a>
            ))}
          </div>
        </RevealOnScroll>
      </div>
    </section>
  );
}
