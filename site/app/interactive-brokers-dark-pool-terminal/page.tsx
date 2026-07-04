import type { Metadata } from "next";
import Link from "next/link";
import { EditorialEyebrow } from "@/components/atoms/EditorialEyebrow";
import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { ScrollProgress } from "@/components/atoms/ScrollProgress";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { SectionRule } from "@/components/atoms/SectionRule";
import { PlateFrame } from "@/components/molecules/PlateFrame";
import { EditorialFooter } from "@/components/sections/EditorialFooter";
import { EditorialHeader } from "@/components/sections/EditorialHeader";
import { DEMO_URL } from "@/lib/editorial-content";
import {
  NAV_LABEL,
  faqEntries,
  ibRoles,
  pageMetadata,
  pageStructuredData,
  requirementRows,
} from "@/lib/pages/interactive-brokers-dark-pool-terminal";

export const metadata: Metadata = pageMetadata();

const crossLink =
  "underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep";

export default function InteractiveBrokersDarkPoolTerminalPage() {
  return (
    <div className="min-h-screen bg-canvas font-serif text-[19px] leading-[1.62] text-primary">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(pageStructuredData()),
        }}
      />
      <ScrollProgress />
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded focus:bg-accent focus:px-4 focus:py-2 focus:text-canvas focus:outline-none"
      >
        Skip to content
      </a>
      <EditorialHeader />
      <main id="main-content">
        {/* Breadcrumb: mirrors the BreadcrumbList JSON-LD exactly. */}
        <nav aria-label="Breadcrumb" className="border-b border-hairline-soft">
          <div className="mx-auto flex max-w-[1140px] flex-wrap items-baseline gap-[10px] px-8 py-[13px] font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted">
            <Link href="/" className="transition-colors hover:text-signal-deep">
              Radon
            </Link>
            <span aria-hidden>/</span>
            <span className="text-signal-deep">{NAV_LABEL}</span>
          </div>
        </nav>

        {/* S1 · Hero */}
        <section className="px-8 pb-[clamp(48px,6vw,80px)] pt-[clamp(56px,7vw,96px)]">
          <div className="mx-auto max-w-[1140px]">
            <RevealOnScroll initiallyShown className="mb-[26px]">
              <EditorialEyebrow>Dossier · Execution</EditorialEyebrow>
            </RevealOnScroll>
            <RevealOnScroll
              initiallyShown
              as="h1"
              className="editorial-display mb-7 max-w-[18ch] text-primary"
            >
              A dark pool terminal is only finished when it routes the order.
            </RevealOnScroll>
            <RevealOnScroll
              initiallyShown
              as="p"
              className="max-w-[66ch] text-[1.22rem] leading-[1.52] text-secondary"
            >
              Most dark pool tools stop at a dashboard: a feed of prints, a
              chart, a conclusion left to the reader. Radon treats the
              off-exchange read as the first half of a pipeline whose last step
              is a routed order on Interactive Brokers rails. The scoping is
              stated plainly up front: Interactive Brokers supplies the
              realtime tape, the account state, and the execution, while
              Unusual Whales supplies the off-exchange prints, because IB does
              not publish a public dark pool print feed.
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S2 · What IB provides */}
        <section id="rails" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="01" label="Interactive Brokers · The Rails" />

            <RevealOnScroll className="mb-[42px] max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                Four jobs, one broker connection.
              </h2>
              <p className="mb-[1.05em] text-secondary">
                Radon&apos;s data source priority is explicit and ordered:
                Interactive Brokers first for realtime data, Unusual Whales
                second for dark pool prints and sweeps, and fallbacks only
                after both have been tried. That ordering shapes the whole
                terminal. IB is not a logo on an integrations page; it is the
                rails the instrument runs on, and it does four distinct jobs.
              </p>
              <p className="mb-[1.05em] text-secondary">
                The first job is the tape. Every off-exchange print that enters
                the flow model is reconciled against the IB realtime feed, so
                the score is anchored to prices the lit market actually
                printed. The second is account state: bankroll, open positions,
                and margin, which feed the fractional Kelly sizing math and the
                hard 2.5% of bankroll per-position cap. The third is execution.
                A structure that clears every milestone is assembled into a
                multi-leg IB combo order, and assembly happens only after the
                mandatory pre-trade risk chokepoint has computed max loss and
                margin impact on the exact combo that will route. The fourth is
                the record: every fill is journaled with lot-matched P&amp;L,
                and that journal is the canonical trade store from which the
                portfolio and order views derive.
              </p>
              <p className="text-secondary">
                One job IB does not do here is supply the dark pool prints
                themselves. That distinction matters enough to get its own
                section.
              </p>
            </RevealOnScroll>

            <RevealOnScroll>
              <PlateFrame
                figNo="Figure 01"
                figTitle="Interactive Brokers roles inside the terminal"
                source="IB TWS / Gateway"
                confidence="Documented"
                caption="The broker connection carries four jobs: reconciliation tape, account state for sizing, combo execution behind the risk chokepoint, and the canonical journal."
              >
                <div className="overflow-x-auto">
                  <table className="scan-table">
                    <thead>
                      <tr>
                        <th>Role</th>
                        <th>Interactive Brokers supplies</th>
                        <th>What the terminal does with it</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ibRoles.map((row) => (
                        <tr key={row.role}>
                          <td className="tk">{row.role}</td>
                          <td className="text-secondary">{row.supplies}</td>
                          <td className="text-secondary">{row.terminalUse}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </PlateFrame>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S3 · Where the dark pool read comes from */}
        <section id="source" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="02" label="Flow · Source and Reconciliation" />

            <RevealOnScroll className="max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                The prints come from off-exchange venues, not from the broker.
              </h2>
              <p className="mb-[1.05em] text-secondary">
                The dark pool read starts with Unusual Whales: off-exchange and
                OTC prints, sweeps, and options flow, pulled from the endpoint
                surface documented in the repo&apos;s Unusual Whales API spec.
                Radon reconciles those prints against the Interactive Brokers
                realtime tape, venue-weights the volume, and normalizes it to a
                rolling z-score. Directional pressure is inferred from
                print-side and size clustering, and the output is a
                threshold-crossing flow score with a lead window measured per
                ticker and surfaced in the scanner.
              </p>
              <p className="mb-[1.05em] text-secondary">
                This page makes no claim that Interactive Brokers exposes dark
                pool data. IB does not offer its customers a public feed of
                dark pool prints, and a terminal that implied otherwise would
                be selling confusion. Radon&apos;s claim is narrower and
                checkable: the prints are Unusual Whales data, the
                reconciliation tape is IB data, and the flow score exists only
                where the two sources agree on what traded.
              </p>
              <p className="text-secondary">
                The full source-to-signal pipeline, endpoint by endpoint, lives
                on its own page:{" "}
                <a
                  href="/unusual-whales-interactive-brokers"
                  className={crossLink}
                >
                  where the prints come from
                </a>
                .
              </p>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S4 · From print to order */}
        <section id="method" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="03" label="Method · Seven Milestones" />

            <RevealOnScroll className="max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                Seven milestones between a print and an order.
              </h2>
              <p className="mb-[1.05em] text-secondary">
                Every trade decision walks the same pipeline, and it stops at
                the first failure. Detect: the flow score crosses its
                accumulation or distribution threshold with a positive lead
                window, and the signal is logged with its source and
                confidence. Corroborate: the read is checked against four
                regime models, and a signal fighting the regime is downgraded,
                not ignored. Locate: dealer gamma is mapped into walls and
                magnets, which set realistic targets and mark the strikes where
                structure resists or accelerates price.
              </p>
              <p className="mb-[1.05em] text-secondary">
                Structure and Size are where the gates bite. The structure must
                be defined-risk with gain at least twice the loss, or the
                evaluation stops at milestone five. Sizing is fractional Kelly
                against live bankroll from the IB account, capped at 2.5% of
                bankroll per position. The evaluation doc calls the cap what it
                is: hard, not advisory.
              </p>
              <p className="mb-[1.05em] text-secondary">
                Verify and Route are the reason this page calls Radon a
                terminal rather than a screener. The mandatory pre-trade
                chokepoint computes max loss, margin impact, and naked-short
                exposure on the assembled combo; it is not optional and cannot
                be bypassed. Only a structure that cleared every prior
                milestone is assembled into an Interactive Brokers BAG combo
                and submitted, and the audit trail records the full chain from
                print to fill. Executed trades append to the trade log, and a
                NO_TRADE outcome is recorded as well.
              </p>
              <p className="text-secondary">
                The stop rule is uniform: fail a gate, name the gate, no order.
                And routing draws from a fixed taxonomy of defined-risk
                structures, catalogued on its own page:{" "}
                <a
                  href="/defined-risk-options-structures"
                  className={crossLink}
                >
                  what is allowed to route
                </a>
                .
              </p>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S5 · Requirements */}
        <section id="requirements" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="04" label="Requirements · What Live Use Needs" />

            <RevealOnScroll className="mb-[42px] max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                What live use needs, and what the demo does not.
              </h2>
              <p className="mb-[1.05em] text-secondary">
                Live use needs three things. A funded Interactive Brokers
                account, because Radon is not a broker and every order executes
                in your own account under IB&apos;s permissions and margin
                rules. An Unusual Whales subscription, because the dark pool
                prints the flow score is built on are UW data. And a running
                TWS or IB Gateway session, which carries the realtime tape and
                the order routing.
              </p>
              <p className="text-secondary">
                The free demo needs none of the three. It runs on seeded data
                with no brokerage connection and shows the flow scanner, the
                regime models, the gate discipline, and the journal as they
                behave in live use.
              </p>
            </RevealOnScroll>

            <RevealOnScroll>
              <PlateFrame
                figNo="Figure 02"
                figTitle="Requirements · live use versus free demo"
                source="Radon"
                confidence="Documented"
                caption="Radon is a research instrument on Interactive Brokers rails, not a broker. The demo carries none of the live requirements."
              >
                <div className="overflow-x-auto">
                  <table className="scan-table">
                    <thead>
                      <tr>
                        <th>Requirement</th>
                        <th>Live use</th>
                        <th>Free demo</th>
                      </tr>
                    </thead>
                    <tbody>
                      {requirementRows.map((row) => (
                        <tr key={row.item}>
                          <td className="tk">{row.item}</td>
                          <td className="text-secondary">{row.liveUse}</td>
                          <td className="text-secondary">{row.demo}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </PlateFrame>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S6 · FAQ: visible rows; the FAQPage JSON-LD mirrors these entries. */}
        <section id="faq" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="05" label="Questions · Plain Answers" />

            <RevealOnScroll className="mb-[42px] max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                Asked before connecting an account.
              </h2>
              <p className="text-secondary">
                The questions Interactive Brokers users bring to a dark pool
                terminal, answered plainly: where the prints come from, what
                routes, and what live use requires.
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

        {/* S7 · Cluster CTA */}
        <section className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <RevealOnScroll>
              <h2 className="editorial-thesis mb-[22px] text-primary">
                Run it against the live tape.
              </h2>
              <p className="mb-[30px] max-w-[66ch] text-secondary">
                The demo runs the same scanner, gates, and journal on seeded
                data. No Interactive Brokers account, no Unusual Whales
                subscription.
              </p>
              <div className="flex flex-wrap items-center gap-[26px]">
                <a
                  href={DEMO_URL}
                  className="inline-block rounded-[4px] border border-grid px-[13px] py-[7px] font-mono text-[11px] uppercase tracking-[0.06em] text-primary transition-colors hover:border-signal-deep hover:text-signal-deep"
                >
                  Try the free demo
                </a>
                <Link
                  href="/#pipeline"
                  className="font-mono text-[12px] uppercase tracking-[0.04em] text-secondary underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep"
                >
                  See the execution discipline &rarr;
                </Link>
              </div>
            </RevealOnScroll>
          </div>
        </section>

        <EditorialFooter />
      </main>
    </div>
  );
}
