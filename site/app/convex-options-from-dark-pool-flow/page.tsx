import Link from "next/link";
import { EditorialEyebrow } from "@/components/atoms/EditorialEyebrow";
import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { ScrollProgress } from "@/components/atoms/ScrollProgress";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { SectionRule } from "@/components/atoms/SectionRule";
import { CopyAgentPromptBar } from "@/components/molecules/CopyAgentPromptBar";
import { PlateFrame } from "@/components/molecules/PlateFrame";
import { EditorialFooter } from "@/components/sections/EditorialFooter";
import { EditorialHeader } from "@/components/sections/EditorialHeader";
import { DEMO_URL } from "@/lib/editorial-content";
import {
  CLUSTER_SLUG,
  NAV_LABEL,
  PAGE_DESCRIPTION,
  PAGE_TITLE,
  clusterPageMetadata,
  confidenceBands,
  faqEntries,
  gateRows,
  interpolationFormulas,
  interpolationRules,
  milestoneDetails,
  pageStructuredData,
  playRows,
  signalBands,
} from "@/lib/pages/convex-options-from-dark-pool-flow";

export const metadata = clusterPageMetadata({
  slug: CLUSTER_SLUG,
  title: PAGE_TITLE,
  description: PAGE_DESCRIPTION,
});

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";

const inlineLink =
  "underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep";

// Local stand-in for the shared ClusterBreadcrumb molecule specified by the
// cluster-page contract. Markup matches the contract exactly so the integrator
// can swap this for components/molecules/ClusterBreadcrumb.tsx unchanged.
function ClusterBreadcrumb() {
  return (
    <nav aria-label="Breadcrumb" className="border-b border-hairline-soft">
      <div className="mx-auto max-w-[1140px] px-8 py-[14px] font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted">
        <Link
          href="/"
          className={`transition-colors hover:text-signal-deep ${focusRing}`}
        >
          Radon
        </Link>{" "}
        <span aria-hidden="true">/</span>{" "}
        <span className="text-signal-deep">{NAV_LABEL}</span>
      </div>
    </nav>
  );
}

// Local stand-in for the shared ClusterCta organism specified by the contract.
function ClusterCta() {
  return (
    <section id="cta" className="px-8 py-[clamp(64px,9vw,128px)]">
      <div className="mx-auto max-w-[1140px]">
        <RevealOnScroll>
          <h2 className="editorial-thesis mb-5 text-primary">
            Run it against the live tape.
          </h2>
          <p className="mb-[34px] max-w-[66ch] text-secondary">
            The demo instance runs the same scanner, the same gates, and the
            same journal on seeded data.
          </p>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-4">
            <a
              href={DEMO_URL}
              className={`inline-block rounded-[4px] border border-grid px-[22px] py-[13px] font-mono text-[12px] uppercase tracking-[0.06em] text-primary transition-colors hover:border-signal-deep hover:text-signal-deep ${focusRing}`}
            >
              Try the free demo
            </a>
            <Link
              href="/#registry"
              className={`font-mono text-[12px] uppercase tracking-[0.06em] text-secondary underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep ${focusRing}`}
            >
              See the plays in the registry
            </Link>
          </div>
        </RevealOnScroll>
      </div>
    </section>
  );
}

export default function ConvexOptionsFromDarkPoolFlowPage() {
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
        <ClusterBreadcrumb />

        {/* S1 · Hero */}
        <section id="top" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <RevealOnScroll initiallyShown className="mb-[26px]">
              <EditorialEyebrow>Dossier · Method</EditorialEyebrow>
            </RevealOnScroll>
            <RevealOnScroll
              initiallyShown
              as="h1"
              className="editorial-display mb-7 max-w-[16ch] text-primary"
            >
              The flow finds the edge. The structure makes it <em>convex</em>.
            </RevealOnScroll>
            <RevealOnScroll
              initiallyShown
              as="p"
              className="max-w-[66ch] text-[1.22rem] leading-[1.52] text-secondary"
            >
              Convexity is selected, not predicted. The print stream nominates
              candidates; structure and sizing decide whether the trade is
              worth taking. The homepage argues that thesis. This page
              publishes the working procedure: seven milestones run in order,
              with a stop on failure at any gate.
            </RevealOnScroll>
            <CopyAgentPromptBar capabilityId="flow" />
          </div>
        </section>

        <SectionRule />

        {/* S2 · The four gates */}
        <section id="gates" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="01" label="Gates · Sequential, no exceptions" />
            <RevealOnScroll className="mb-[42px] max-w-[66ch]">
              <h2 className="editorial-thesis mb-5 text-primary">
                Four gates, run in order.
              </h2>
              <p className="mb-5 text-secondary">
                Every candidate the flow nominates is forced through four
                sequential gates before a contract exists. The order matters:
                convexity is checked before edge, edge before size, and a
                failure at any gate stops the evaluation and names itself.
                There is no override that lets conviction outrank structure,
                and there is no partial credit. A trade that clears three gates
                and fails one is not a trade.
              </p>
              <p className="text-secondary">
                The fourth gate, the naked-short block, is currently disabled
                by operator policy. The rule stays published because the method
                is published: the blocking logic is preserved in the codebase
                and the re-enable path is documented. A disabled gate that
                remains visible is a different thing from a gate that quietly
                disappears.
              </p>
              <CopyAgentPromptBar capabilityId="gates" />
              <div className="mt-4 grid gap-3 sm:grid-cols-2">
                <CopyAgentPromptBar capabilityId="gate-01" className="" />
                <CopyAgentPromptBar capabilityId="gate-02" className="" />
                <CopyAgentPromptBar capabilityId="gate-03" className="" />
                <CopyAgentPromptBar capabilityId="gate-04" className="" />
              </div>
            </RevealOnScroll>
            <RevealOnScroll>
              <PlateFrame
                figNo="Exhibit A"
                figTitle="The four gates · any failure stops the trade and names the gate"
                source="Method spec"
                confidence="Verbatim"
              caption="The gate table as the method states it. Gates one through three are active; gate four is disabled by operator policy with its logic preserved."
              >
                <div className="overflow-x-auto">
                  <table className="scan-table min-w-[560px]">
                    <thead>
                      <tr>
                        <th>Gate</th>
                        <th>Rule</th>
                        <th>Condition</th>
                      </tr>
                    </thead>
                    <tbody>
                      {gateRows.map((gate) => (
                        <tr
                          key={gate.no}
                          className={gate.disabled ? "opacity-60" : undefined}
                        >
                          <td className="tk whitespace-nowrap">
                            {gate.no} · {gate.name}
                          </td>
                          <td
                            className={`whitespace-nowrap ${
                              gate.disabled
                                ? "text-muted line-through"
                                : "text-signal-deep"
                            }`}
                          >
                            {gate.rule}
                          </td>
                          <td className="font-serif text-[14px]">
                            {gate.condition}
                          </td>
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

        {/* S3 · Seven milestones */}
        <section id="milestones" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="02" label="Method · Candidate to routed order" />
            <RevealOnScroll className="mb-10 max-w-[66ch]">
              <h2 className="editorial-thesis mb-5 text-primary">
                Seven milestones, candidate to routed order.
              </h2>
              <p className="text-secondary">
                The gates say what must be true. The milestones say when each
                check runs. A candidate enters at milestone one and either
                exits as a routed, journaled order or stops at the first
                milestone it cannot clear. Nothing skips ahead, and nothing
                that fails late gets to retry early.
              </p>
            </RevealOnScroll>
            <RevealOnScroll className="pipeline">
              {milestoneDetails.map((milestone) => (
                <div
                  key={milestone.name}
                  className="grid grid-cols-[40px_1fr] items-start gap-[18px] border-b border-hairline-soft py-[26px] sm:grid-cols-[56px_1fr] sm:gap-7"
                >
                  <span
                    aria-hidden="true"
                    className="milestone-no pt-[5px] font-mono text-[13px] font-semibold text-signal-deep"
                  />
                  <div>
                    <h3 className="mb-[7px] font-serif text-[1.32rem] font-medium tracking-[-0.01em] text-primary">
                      {milestone.name}
                    </h3>
                    <p className="m-0 max-w-[66ch] text-[1.02rem] leading-[1.55] text-secondary">
                      {milestone.body}
                    </p>
                    <span className="mt-2 inline-block font-mono text-[10.5px] uppercase tracking-[0.06em] text-muted">
                      {milestone.tag}
                    </span>
                    {milestone.reference ? (
                      <span className="mt-2 block">
                        <a
                          href={milestone.reference.href}
                          className={`font-mono text-[11.5px] tracking-[0.03em] text-secondary ${inlineLink} ${focusRing}`}
                        >
                          {milestone.reference.label}
                        </a>
                      </span>
                    ) : null}
                  </div>
                </div>
              ))}
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S4 · Intraday interpolation */}
        <section id="interpolation" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="03" label="Flow · Reading a partial session" />
            <RevealOnScroll className="mb-[42px] max-w-[66ch]">
              <h2 className="editorial-thesis mb-5 text-primary">
                Reading a partial session.
              </h2>
              <p className="text-secondary">
                Dark pool prints accumulate through the session, so a raw total
                read at 11:00 says little about the day. The method handles
                this with a published interpolation: project the full session
                from elapsed minutes, blend the projection with the prior
                five-day average, and weight the blend by how much of the
                session has actually printed. Early in the day the prior
                dominates; late in the day the tape speaks for itself.
              </p>
            </RevealOnScroll>
            <RevealOnScroll className="mb-[42px]">
              <PlateFrame
                figNo="Exhibit B"
                figTitle="Intraday dark-pool interpolation · the published math"
                source="Method spec"
                confidence="Verbatim"
                caption="The interpolation as the evaluation spec states it: the projection, the prior-weighted blend, the pace check, and the confidence grade by session progress."
              >
                <div className="overflow-x-auto">
                  <div className="min-w-[520px]">
                    <dl className="m-0 border-b border-hairline-soft pb-4 font-mono text-[12.5px] leading-[1.9]">
                      {interpolationFormulas.map((formula) => (
                        <div key={formula.label} className="flex flex-wrap gap-x-3">
                          <dt className="w-[88px] flex-shrink-0 text-signal-deep">
                            {formula.label}
                          </dt>
                          <dd className="m-0 text-secondary">
                            = {formula.expression}
                          </dd>
                        </div>
                      ))}
                    </dl>
                    <table className="scan-table mt-4">
                      <thead>
                        <tr>
                          <th>Session progress</th>
                          <th>Confidence</th>
                          <th className="text-right">Prior weight</th>
                        </tr>
                      </thead>
                      <tbody>
                        {confidenceBands.map((band) => (
                          <tr key={band.progress}>
                            <td className="num text-left">{band.progress}</td>
                            <td className="tk">{band.confidence}</td>
                            <td className="num">{band.priorWeight}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </PlateFrame>
            </RevealOnScroll>
            <RevealOnScroll className="max-w-[66ch]">
              <ul className="m-0 list-none border-t border-hairline-soft p-0">
                {interpolationRules.map((rule) => (
                  <li
                    key={rule}
                    className="border-b border-hairline-soft py-[14px] text-[1.02rem] leading-[1.55] text-secondary"
                  >
                    {rule}
                  </li>
                ))}
              </ul>
              <p className="mt-6 text-secondary">
                The output carries its own confidence grade, and the grade has
                teeth: a LOW or VERY_LOW read is not traded on, it is held for
                re-evaluation in the afternoon. Both figures, actual and
                interpolated, are always reported together, because a blended
                number that hides its inputs is a number you cannot audit.
              </p>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S5 · Signal interpretation bands */}
        <section id="signals" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="04" label="Signals · Thresholds, stated" />
            <RevealOnScroll className="mb-[42px] max-w-[66ch]">
              <h2 className="editorial-thesis mb-5 text-primary">
                Thresholds, stated.
              </h2>
              <p className="text-secondary">
                A method that says bearish flow without saying where bearish
                begins is a mood, not a method. The bands below are the
                published thresholds the evaluation uses. They make any single
                read checkable after the fact: the inputs are on the tape, the
                bands are on this page, and the conclusion either follows or it
                does not.
              </p>
            </RevealOnScroll>
            <RevealOnScroll className="mb-[42px]">
              <PlateFrame
                figNo="Exhibit C"
                figTitle="Signal interpretation bands · P/C ratio, flow side, ratings, seasonality"
                source="Method spec"
                confidence="Verbatim"
                caption="Interpretation bands as published in the evaluation spec. Seasonality and analyst ratings are context inputs; the flow signals are the gates."
              >
                <div className="overflow-x-auto">
                  <table className="scan-table min-w-[560px]">
                    <thead>
                      <tr>
                        <th>Signal</th>
                        <th>Bands</th>
                      </tr>
                    </thead>
                    <tbody>
                      {signalBands.map((row) => (
                        <tr key={row.signal}>
                          <td className="tk whitespace-nowrap align-top">
                            {row.signal}
                          </td>
                          <td>
                            <span className="flex flex-wrap gap-x-5 gap-y-1">
                              {row.bands.map((band) => (
                                <span
                                  key={band.range}
                                  className="whitespace-nowrap"
                                >
                                  {band.range}{" "}
                                  <b className="font-medium text-signal-deep">
                                    {band.read}
                                  </b>
                                </span>
                              ))}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </PlateFrame>
            </RevealOnScroll>
            <RevealOnScroll className="max-w-[66ch]">
              <p className="text-secondary">
                One caveat is part of the spec rather than a footnote:
                seasonality and analyst ratings are context, not gates. They
                frame a candidate at milestone one, but strong flow overrides
                weak seasonality, and no ratings consensus can pass or fail an
                edge decision on its own.
              </p>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S6 · The plays */}
        <section id="plays" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="05" label="Registry · Six repeatable expressions" />
            <RevealOnScroll className="mb-[42px] max-w-[66ch]">
              <h2 className="editorial-thesis mb-5 text-primary">
                Six repeatable expressions.
              </h2>
              <p className="text-secondary">
                Run the method long enough and the outputs cluster. The
                registry names six recurring expressions: each is a specific
                edge source mapped to a defined-risk structure with a convexity
                floor the trade must clear at entry. They are worked outputs of
                the seven milestones, not independent strategies. Every one of
                them runs the same gates, the same sizing, and the same
                journal.
              </p>
            </RevealOnScroll>
            <RevealOnScroll className="mb-[42px]">
              <PlateFrame
                figNo="Exhibit D"
                figTitle="The registry plays · edge, structure, convexity floor"
                source="Registry"
                confidence="Defined risk"
                caption="Each play maps an edge source to its structure and the minimum convexity the trade must clear at entry. All six are defined-risk."
              >
                <div className="overflow-x-auto">
                  <table className="scan-table min-w-[560px]">
                    <thead>
                      <tr>
                        <th>Play</th>
                        <th>Edge source</th>
                        <th>Structure</th>
                        <th className="text-right">Convexity floor</th>
                      </tr>
                    </thead>
                    <tbody>
                      {playRows.map((play) => (
                        <tr key={play.name}>
                          <td className="tk whitespace-nowrap">
                            {play.href ? (
                              <a href={play.href} className={inlineLink}>
                                {play.name}
                              </a>
                            ) : (
                              play.name
                            )}
                          </td>
                          <td>{play.edge}</td>
                          <td className="whitespace-nowrap">{play.structure}</td>
                          <td className="num text-signal-deep">
                            {play.convexityFloor}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </PlateFrame>
            </RevealOnScroll>
            <RevealOnScroll className="max-w-[66ch]">
              <p className="text-secondary">
                The floors differ because the edges differ. A tail hedge bought
                when the{" "}
                <a href="/crash-risk-index" className={inlineLink}>
                  Crash Risk Index
                </a>{" "}
                is elevated demands more convexity than a pin structure sold
                into a dealer gamma wall, because the hedge pays rarely and
                must pay large when it does. The structures themselves come
                from the same{" "}
                <a href="/defined-risk-options-structures" className={inlineLink}>
                  defined-risk catalog
                </a>{" "}
                that milestone five selects from.
              </p>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S7 · FAQ */}
        <section id="faq" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="06" label="Appendix · Questions" />
            <RevealOnScroll className="mb-[42px] max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                Asked about the method.
              </h2>
              <p className="text-secondary">
                Four questions that come up when the procedure is read closely,
                answered plainly.
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

        {/* S8 · CTA */}
        <ClusterCta />

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
