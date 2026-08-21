import { NOT_FOUND_RECOVERY_LINKS } from "@/lib/not-found-recovery";

// Branded 404 in the Editorial voice. Pure JSX + plain <a> — no next/link,
// no hooks — mirroring the global-error constraint (see web/CLAUDE.md
// "Production Build Constraint"); renders under the root layout so the
// theme tokens and fonts apply.
export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-start justify-center bg-canvas px-8 text-primary">
      <div className="mx-auto w-full max-w-[1140px]">
        <p className="editorial-eyebrow mb-[26px]">Page not found</p>
        <h1 className="editorial-display mb-7 max-w-[16ch]">
          This page is <em>not</em> in the registry.
        </h1>
        <p className="mb-10 max-w-[52ch] font-serif text-[1.22rem] leading-[1.52] text-secondary">
          The address resolves to nothing we publish. Recover from the sitemap,
          the agent index, or the developer resources.
        </p>
        <ul className="mb-10 max-w-[52ch] space-y-3 font-mono text-[13px] leading-[1.5] text-secondary">
          {NOT_FOUND_RECOVERY_LINKS.map((link) => (
            <li key={link.href}>
              {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- error
                  surfaces stay off next/link per the production build constraint */}
              <a
                href={link.href}
                className="text-signal-deep underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas"
              >
                {link.label}
              </a>
              <span className="text-muted"> · {link.note}</span>
            </li>
          ))}
        </ul>
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- error
            surfaces stay off next/link per the production build constraint */}
        <a
          href="/"
          className="inline-block rounded-[4px] border border-signal-deep bg-signal-deep px-[22px] py-[13px] font-mono text-[12px] uppercase tracking-[0.06em] text-canvas transition-colors hover:bg-transparent hover:text-signal-deep focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas"
        >
          Back to the front page
        </a>
      </div>
    </main>
  );
}
