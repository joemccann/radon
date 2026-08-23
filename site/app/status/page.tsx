import type { Metadata } from "next";
import Link from "next/link";
import { EditorialEyebrow } from "@/components/atoms/EditorialEyebrow";
import { EditorialFooter } from "@/components/sections/EditorialFooter";
import { EditorialHeader } from "@/components/sections/EditorialHeader";
import { DEMO_APP_URL, STATUS_PAGE_ROBOTS, siteUrl } from "@/lib/seo";

/**
 * Public system-surface map (P3).
 *
 * Not a replica of the operator dashboard and not a Turso Edge reader of
 * production account data. Explains where public vs operator surfaces live so
 * the "Vercel Edge public dashboard" gap is closed without shipping PII.
 */
export const metadata: Metadata = {
  title: "System surfaces | Radon Terminal",
  description:
    "Where Radon public and operator surfaces live: marketing site, free demo, and authenticated terminal.",
  alternates: { canonical: `${siteUrl}/status` },
  robots: STATUS_PAGE_ROBOTS,
};

// Static page — Edge-safe, no Node-only APIs, no account data.
export const dynamic = "force-static";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";

const surfaces = [
  {
    name: "Marketing site",
    url: "https://radon.run",
    host: "Vercel",
    data: "Editorial content only. No portfolio, journal, or account figures.",
  },
  {
    name: "Public demo",
    url: DEMO_APP_URL,
    host: "Vercel + isolated Turso",
    data: "Synthetic positions and paper fills. Separate database from production.",
  },
  {
    name: "Operator terminal",
    url: "https://app.radon.run",
    host: "Hetzner + Clerk",
    data: "Live IB, Turso journal, service health. Authenticated allowlist only.",
  },
] as const;

export default function StatusPage() {
  return (
    <>
      <EditorialHeader />
      <main className="bg-canvas text-ink">
        <nav aria-label="Breadcrumb" className="border-b border-hairline-soft">
          <div className="mx-auto flex max-w-[1140px] items-center gap-[10px] px-8 py-[13px] font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted">
            <Link href="/" className={`transition-colors hover:text-signal-deep ${focusRing}`}>
              Radon
            </Link>
            <span aria-hidden="true">/</span>
            <span className="text-signal-deep">Status</span>
          </div>
        </nav>

        <section className="mx-auto max-w-[720px] px-8 pb-24 pt-16">
          <EditorialEyebrow>Public surfaces</EditorialEyebrow>
          <h1 className="mt-4 font-serif text-[clamp(2rem,4vw,2.75rem)] leading-[1.15] tracking-[-0.02em] text-ink">
            Where Radon runs in public
          </h1>
          <p className="mt-6 text-[17px] leading-[1.65] text-muted">
            There is no Edge replica of the production Turso store. Account
            figures stay on the authenticated terminal. Public product
            experience is the free demo with synthetic data.
          </p>

          <ul className="mt-12 space-y-0 border-t border-hairline-soft">
            {surfaces.map((surface) => (
              <li
                key={surface.name}
                className="border-b border-hairline-soft py-6"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-3">
                  <h2 className="font-serif text-[1.35rem] text-ink">
                    {surface.name}
                  </h2>
                  <span className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">
                    {surface.host}
                  </span>
                </div>
                <a
                  href={surface.url}
                  className={`mt-2 inline-block font-mono text-[13px] text-signal-deep underline-offset-4 hover:underline ${focusRing}`}
                >
                  {surface.url.replace(/^https:\/\//, "")}
                </a>
                <p className="mt-3 text-[15px] leading-[1.55] text-muted">
                  {surface.data}
                </p>
              </li>
            ))}
          </ul>

          <p className="mt-10 font-mono text-[12px] leading-[1.5] text-muted">
            Operator reliability metrics (service health, journal gap SLI,
            backups) are on the authenticated admin surface — not published
            here.
          </p>
        </section>
      </main>
      <EditorialFooter />
    </>
  );
}
