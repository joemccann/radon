import Link from "next/link";
import { EditorialEyebrow } from "@/components/atoms/EditorialEyebrow";
import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { ScrollProgress } from "@/components/atoms/ScrollProgress";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { SectionRule } from "@/components/atoms/SectionRule";
import { MilestoneRow } from "@/components/molecules/MilestoneRow";
import { PlateFrame } from "@/components/molecules/PlateFrame";
import { EditorialFooter } from "@/components/sections/EditorialFooter";
import { EditorialHeader } from "@/components/sections/EditorialHeader";
import { DEMO_URL, IB_URL, UW_URL } from "@/lib/editorial-content";
import {
  faqEntries,
  ingestSurfaces,
  NAV_LABEL,
  pageMetadata,
  pageStructuredData,
  pipelineStages,
  requirementRows,
} from "@/lib/pages/unusual-whales-interactive-brokers";

export const metadata = pageMetadata;

const inlineLink =
  "underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep";

// Inline equivalent of the shared ClusterBreadcrumb molecule (contract markup).
// Integrator: swap to components/molecules/ClusterBreadcrumb.tsx once it lands.
// Mirrors the BreadcrumbList JSON-LD in the page content module exactly.
function Breadcrumb() {
  return (
    <nav aria-label="Breadcrumb" className="border-b border-hairline-soft">
      <div className="mx-auto max-w-[1140px] px-8 py-[14px] font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted">
        <Link href="/" className="transition-colors hover:text-signal-deep">
          Radon
        </Link>{" "}
        <span aria-hidden="true">/</span>{" "}
        <span className="text-signal-deep">{NAV_LABEL}</span>
      </div>
    </nav>
  );
}

// Inline equivalent of the shared ClusterCta organism (contract markup).
// Integrator: swap to components/sections/ClusterCta.tsx once it lands.
function BottomCta() {
  return (
    <section className="px-8 py-[clamp(64px,9vw,128px)]">
      <div className="mx-auto max-w-[1140px]">
        <RevealOnScroll>
          <h2 className="editorial-thesis mb-[18px] text-primary">
            Run it against the live tape.
          </h2>
          <p className="mb-9 max-w-[66ch] text-secondary">
            The demo carries the full instrument on seeded data: scanner, gates,
            sizing, journal. No subscription to either service required.
          </p>
          <div className="flex flex-wrap items-center gap-7">
            <a
              href={DEMO_URL}
              className="inline-block rounded-[4px] border border-grid px-[13px] py-[7px] font-mono text-[11px] uppercase tracking-[0.06em] text-primary transition-colors hover:border-signal-deep hover:text-signal-deep"
            >
              Try the free demo
            </a>
            <Link
              href="/#flow"
              className="font-mono text-[11px] uppercase tracking-[0.06em] text-secondary underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep"
            >
              Read the flow thesis
            </Link>
          </div>
        </RevealOnScroll>
      </div>
    </section>
  );
}

export default function UnusualWhalesInteractiveBrokersPage() {
  return (
    <div className="min-h-screen bg-canvas font-serif text-[19px] leading-[1.62] text-primary">
      <ScrollProgress />
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded focus:bg-accent focus:px-4 focus:py-2 focus:text-canvas focus:outline-none"
      >
        Skip to content
      </a>
      <EditorialHeader />
      <main id="main-content">
        <Breadcrumb />

        <section
          id="top"
          className="px-8 pb-[clamp(48px,6vw,80px)] pt-[clamp(56px,7vw,96px)]"
        >
          <div className="mx-auto max-w-[1140px]">
            <RevealOnScroll initiallyShown className="mb-[26px]">
              <EditorialEyebrow>Dossier · Integration</EditorialEyebrow>
            </RevealOnScroll>
            <RevealOnScroll
              initiallyShown
              as="h1"
              className="editorial-display mb-7 max-w-[22ch] text-primary"
            >
              Unusual Whales prints in. Interactive Brokers orders out.
            </RevealOnScroll>
            <RevealOnScroll
              initiallyShown
              as="p"
              className="max-w-[66ch] text-[1.22rem] leading-[1.52] text-secondary"
            >
              An Unusual Whales subscription produces reads; an Interactive
              Brokers account produces fills. This page documents the
              disciplined pipeline between the two: the surfaces Radon ingests,
              the stages a print passes through on its way to an order, and
              what each side requires. The instrument itself is covered
              separately, in{" "}
              <a
                href="/interactive-brokers-dark-pool-terminal"
                className={inlineLink}
              >
                the terminal built on these rails
              </a>
              .
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        <section id="ingest" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="01" label="Ingest · Prints, Sweeps, Options Flow" />

            <div className="grid items-start gap-[clamp(36px,5vw,72px)] md:grid-cols-2">
              <RevealOnScroll>
                <h2 className="editorial-thesis mb-[22px] text-primary">
                  Four surfaces in, one score out.
                </h2>
                <p className="mb-[1.05em] text-secondary">
                  Radon&apos;s evaluation pipeline names its inputs explicitly.
                  Dark pool and OTC prints are milestone two of the
                  seven-milestone method. Options flow is milestone three, with
                  open-interest changes as a required sub-step, not an optional
                  one. Seasonality enters early and stays in its place: it is
                  context that can color a read, it never gates a trade, and
                  strong flow overrides weak seasonality by rule. The endpoint
                  surface behind these inputs is documented as a fixed contract
                  in the project&apos;s Unusual Whales API specification, so
                  what the scanner consumes is enumerable rather than vague.
                </p>
                <p className="mb-[1.05em] text-secondary">
                  None of it is trusted alone. Every Unusual Whales surface is
                  reconciled against the Interactive Brokers realtime tape
                  before scoring, and the integration runs in that order by
                  design: Interactive Brokers first for the live tape, Unusual
                  Whales second for the off-exchange record the tape cannot
                  see. During market hours a partial session&apos;s dark pool
                  volume is interpolated by session progress, and both the
                  actual and the interpolated figures are always reported
                  together, with confidence graded from very low in the opening
                  minutes to high in the final quarter of the session.
                </p>
              </RevealOnScroll>

              <RevealOnScroll>
                <div className="border-t border-hairline-soft">
                  {ingestSurfaces.map((surface) => (
                    <div
                      key={surface.label}
                      className="grid grid-cols-[auto_1fr] gap-4 border-b border-hairline-soft py-4"
                    >
                      <span className="whitespace-nowrap pt-1 font-mono text-[10.5px] uppercase tracking-[0.12em] text-signal-deep">
                        {surface.label}
                      </span>
                      <p className="m-0 text-[1.02rem] leading-[1.5] text-secondary">
                        {surface.body}
                      </p>
                    </div>
                  ))}
                </div>
              </RevealOnScroll>
            </div>
          </div>
        </section>

        <SectionRule />

        <section id="pipeline" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading
              no="02"
              label="Pipeline · Print to Order in Seven Milestones"
            />

            <RevealOnScroll className="mb-10 max-w-[66ch]">
              <h2 className="editorial-thesis mb-5 text-primary">
                What sits between a print and a fill.
              </h2>
              <p className="text-[1.32rem] leading-[1.5] text-primary">
                A print that arrives without discipline is a temptation. A
                print that arrives through these stages is either a
                defined-risk order or nothing. The stages run in order, each
                has a pass condition, and a failure at any point stops the
                trade with the failing stage named. The thresholds each stage
                applies are documented in{" "}
                <a
                  href="/convex-options-from-dark-pool-flow"
                  className={inlineLink}
                >
                  the full method with thresholds
                </a>
                .
              </p>
            </RevealOnScroll>

            <RevealOnScroll className="pipeline">
              {pipelineStages.map((stage) => (
                <MilestoneRow key={stage.name} milestone={stage} />
              ))}
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        <section id="requirements" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading
              no="03"
              label="Requirements · Two Subscriptions, One Discipline"
            />

            <div className="grid items-start gap-[clamp(36px,5vw,72px)] md:grid-cols-2">
              <RevealOnScroll>
                <h2 className="editorial-thesis mb-[22px] text-primary">
                  What each side supplies.
                </h2>
                <p className="mb-[1.05em] text-secondary">
                  The live instrument stands on two commercial relationships,
                  both of them yours rather than Radon&apos;s. A subscription
                  to{" "}
                  <a
                    href={UW_URL}
                    target="_blank"
                    rel="sponsored noopener noreferrer"
                    className={inlineLink}
                  >
                    Unusual Whales
                  </a>{" "}
                  supplies the dark pool prints, sweeps, and options flow that
                  scoring is built on; that link is a referral link, and the
                  referral is the extent of the relationship. A funded{" "}
                  <a
                    href={IB_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={inlineLink}
                  >
                    Interactive Brokers
                  </a>{" "}
                  account supplies the realtime tape, the margin, and the order
                  routing: orders go out under your account, not through an
                  intermediary. Radon is not affiliated with either company
                  beyond being a customer of both.
                </p>
                <p className="mb-[1.05em] text-secondary">
                  The demo requires neither. It runs the full instrument
                  against seeded data, with no brokerage connection and no flow
                  subscription, so the pipeline can be inspected before either
                  relationship exists.
                </p>
              </RevealOnScroll>

              <RevealOnScroll>
                <PlateFrame
                  figNo="Exhibit 1"
                  figTitle="Requirements · what each component supplies"
                  source="Radon"
                  confidence="High"
                  caption="Two subscriptions feed the live instrument; the demo at demo.radon.run needs neither. The Unusual Whales link on this page is a referral link."
                >
                  <div className="overflow-x-auto">
                    <table className="scan-table">
                      <thead>
                        <tr>
                          <th>Component</th>
                          <th>Supplies</th>
                          <th>Required for</th>
                        </tr>
                      </thead>
                      <tbody>
                        {requirementRows.map((row) => (
                          <tr key={row.component}>
                            <td className="tk">{row.component}</td>
                            <td>{row.supplies}</td>
                            <td>{row.requiredFor}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </PlateFrame>
              </RevealOnScroll>
            </div>
          </div>
        </section>

        <SectionRule />

        <section id="method" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="04" label="Method · Two Sources Beat One" />

            <RevealOnScroll className="max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                Why prints are scored against the tape.
              </h2>
              <p className="mb-[1.05em] text-secondary">
                A print, taken alone, states a venue, a size, and a price. It
                does not state intent, and it does not state whether the market
                has already absorbed it. Scoring Unusual Whales prints against
                the Interactive Brokers realtime tape closes both gaps. Venue
                weighting exists because venues differ in what a print there
                tends to mean, and normalizing volume to a rolling z-score
                keeps a loud session from masquerading as a signal. Direction
                comes from print-side and size clustering across the stream,
                not from any single print.
              </p>
              <p className="mb-[1.05em] text-secondary">
                The reconciliation is also the false-positive filter. A print
                whose implication is already visible in the lit price fails the
                edge gate by definition: the requirement is a specific,
                data-backed signal that has not yet moved price. Checking the
                feed against the tape is how that requirement becomes testable
                rather than rhetorical.
              </p>
              <p className="mb-[1.05em] text-secondary">
                And the lead is measured, not assumed. The scanner reports a
                lead window per ticker, derived from when the print stream
                turned against when the lit price followed. A flow score that
                misses its threshold is discarded, whatever the chart looks
                like. That refusal is the discipline the rest of the pipeline
                inherits.
              </p>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        <section id="faq" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="05" label="Appendix · Plain Answers" />

            <RevealOnScroll className="mb-[42px] max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                Asked about the integration.
              </h2>
              <p className="text-secondary">
                What the two subscriptions cover, what the demo covers, and
                where Radon stands relative to both companies.
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

        <SectionRule />

        <BottomCta />
        <EditorialFooter />
      </main>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(pageStructuredData()),
        }}
      />
    </div>
  );
}
