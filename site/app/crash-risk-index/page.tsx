import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
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
  crashRiskIndexFaqEntries,
  crashRiskIndexMetadata,
  crashRiskIndexStructuredData,
  CRASH_RISK_INDEX_NAV_LABEL,
  criComponents,
  criSignalBands,
} from "@/lib/pages/crash-risk-index";

export const metadata: Metadata = crashRiskIndexMetadata;

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";

const inlineLink =
  "underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep";

const DEMO_HOST = "demo.radon.run";

// Linkifies demo.radon.run mentions on-page only; the FAQPage JSON-LD keeps
// the plain-text answer from the content module (same as sections/FaqSection).
function linkifyDemoMentions(answer: string): ReactNode[] {
  return answer.split(DEMO_HOST).flatMap((segment, index) =>
    index === 0
      ? [segment]
      : [
          <a key={`demo-${index}`} href={DEMO_URL} className={inlineLink}>
            {DEMO_HOST}
          </a>,
          segment,
        ],
  );
}

// Local stand-in for the shared ClusterBreadcrumb molecule; markup follows the
// cluster-page contract so the integrator can swap it in without visual change.
function ClusterBreadcrumb({ navLabel }: { navLabel: string }) {
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

function InputRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-4 border-t border-hairline-soft py-[30px] md:grid-cols-[220px_1fr] md:gap-8">
      <span className="font-mono text-[11.5px] uppercase tracking-[0.14em] text-signal-deep">
        {label}
      </span>
      <div className="max-w-[66ch] space-y-4 text-secondary">{children}</div>
    </div>
  );
}

export default function CrashRiskIndexPage() {
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
        <ClusterBreadcrumb navLabel={CRASH_RISK_INDEX_NAV_LABEL} />

        {/* S1 · Hero */}
        <section
          id="overview"
          className="px-8 pb-[clamp(48px,6vw,80px)] pt-[clamp(56px,7vw,96px)]"
        >
          <div className="mx-auto max-w-[1140px]">
            <RevealOnScroll initiallyShown className="mb-[26px]">
              <EditorialEyebrow>Dossier · Regime</EditorialEyebrow>
            </RevealOnScroll>

            <RevealOnScroll
              initiallyShown
              as="h1"
              className="editorial-display mb-7 max-w-[22ch] text-primary"
            >
              Forced selling is <em>mechanical</em>. The Crash Risk Index reads
              the regime that triggers it.
            </RevealOnScroll>

            <RevealOnScroll
              initiallyShown
              as="p"
              className="max-w-[66ch] text-[1.22rem] leading-[1.52] text-secondary"
            >
              CRI is Radon&apos;s Crash Risk Index: a 0 to 100 crash regime
              read built from four components, each scored 0 to 25: VIX, VVIX,
              implied correlation, and SPX trend against its 100-day moving
              average. The inputs, the weights, and the thresholds are
              published here in full.
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S2 · The three inputs */}
        <section id="inputs" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="01" label="Components · What the index reads" />

            <RevealOnScroll className="mb-10 max-w-[66ch]">
              <h2 className="editorial-thesis text-primary">
                Four measurements, each scored 0 to 25.
              </h2>
              <p className="mt-5 text-secondary">
                The index exists because roughly $400 billion of systematic
                CTA money targets a fixed portfolio volatility: when realized
                vol doubles, those funds must halve their equity exposure, and
                the selling that follows is mechanical, not discretionary. CRI
                reads the four conditions that regime converges on and sums
                them into a single 0 to 100 index.
              </p>
            </RevealOnScroll>

            <RevealOnScroll>
              <div className="border-b border-hairline-soft">
                <InputRow label="Component 01 · VIX">
                  <p>
                    The first component reads the level of the VIX on a linear
                    band from 15 to 40, plus its 5-day rate of change, scored
                    from flat to a 60 percent rise. Level and speed are scored
                    separately because they say different things: a high VIX is
                    a stressed regime, and a fast-rising VIX is a repricing
                    under way. Vol-targeting funds key their exposure off this
                    complex, so when it reprices, their selling math changes
                    with it.
                  </p>
                </InputRow>
                <InputRow label="Component 02 · VVIX">
                  <p>
                    VVIX is the volatility of the VIX itself: what the market
                    pays for options on volatility. The second component scores
                    the VVIX level on a linear band from 90 to 140, plus the
                    VVIX to VIX ratio, where a ratio above 8 reads as demand
                    for convexity on vol itself. A rising VVIX over a quiet VIX
                    means the vol complex is being repriced at the second
                    order, before the front number moves.
                  </p>
                </InputRow>
                <InputRow label="Component 03 · Correlation">
                  <p>
                    COR1M is the Cboe 1-Month Implied Correlation Index: how
                    much the options market expects the top 50 S&amp;P names to
                    move together. The third component scores the level on a
                    linear band from 25 to 70, plus the 5-session change, so a
                    spike registers even from a low base. A correlation spike
                    means diversification is breaking down: the market is
                    pricing the index as one trade.
                  </p>
                </InputRow>
                <InputRow label="Component 04 · Momentum">
                  <p>
                    The fourth component is trend: SPX distance below its
                    100-day moving average, scored linearly from zero at the
                    average to the full 25 points at 10 percent below. Above
                    the average it scores nothing. This is the component
                    trend-following CTAs act on directly: below the 100-day,
                    systematic trend models flip from long to short, and the
                    flow that follows is mechanical.
                  </p>
                </InputRow>
              </div>
            </RevealOnScroll>

            <RevealOnScroll as="p" className="mt-10 max-w-[66ch] text-secondary">
              Nothing in the composite is withheld. The four components carry
              equal 25-point weights, the thresholds are hand-set linear
              bands, and the scores sum to the 0 to 100 index. The claim is
              not a secret formula. The claim is that these four conditions
              are the ones that force systematic selling, and that reading
              them together, level and speed at once, catches a regime forming
              before any single number looks alarming on its own.
            </RevealOnScroll>

            <RevealOnScroll className="mt-12">
              <PlateFrame
                figNo="Figure 1"
                figTitle="CRI · the four published components"
                source="Method note"
                confidence="Documented"
                caption="The four components of the Crash Risk Index. Each is scored 0 to 25 on published linear thresholds; the scores sum to the 0 to 100 composite."
              >
                <div className="overflow-x-auto">
                  <table className="registry" style={{ minWidth: 640 }}>
                    <thead>
                      <tr>
                        <th>Component</th>
                        <th>Weight</th>
                        <th>Inputs</th>
                        <th>Read when it scores</th>
                      </tr>
                    </thead>
                    <tbody>
                      {criComponents.map((component) => (
                        <tr key={component.name}>
                          <td className="name">{component.name}</td>
                          <td>{component.weight}</td>
                          <td>{component.inputs}</td>
                          <td className="mech">{component.read}</td>
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

        {/* S3 · A regime read, not a prediction */}
        <section id="semantics" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="02" label="Semantics · Bands, not alarms" />

            <RevealOnScroll className="mb-10 max-w-[66ch]">
              <h2 className="editorial-thesis text-primary">
                A regime read, not a prediction.
              </h2>
              <p className="mt-5 text-secondary">
                CRI outputs a regime state, not a forecast. The distinction is
                the whole design. A forecast names a direction and implies a
                date. A regime state describes the present: what the four
                components read now, and how far that reading sits from its
                baseline. The score maps to four published bands, from low,
                a normal regime with no systematic risk, through elevated and
                high, to critical, an active crash regime.
              </p>
            </RevealOnScroll>

            <RevealOnScroll as="p" className="mb-12 max-w-[66ch] text-secondary">
              The scale is deliberately unemotional. Radon&apos;s alert
              language says &ldquo;Structural event detected.&rdquo; It does
              not say &ldquo;crash incoming,&rdquo; because the instrument does
              not know that, and pretending otherwise would be a claim the data
              cannot carry. An elevated CRI band means one or more components
              is stressed; a high band means several have triggered at once
              and mechanical CTA selling is likely close. Nothing in the index
              knows the date, and elevated readings can recede without a
              drawdown; vol spikes and then relaxes. What the band does is
              change behavior. It changes which signals the terminal trusts,
              and it changes what a hedge costs relative to what it covers.
            </RevealOnScroll>

            <RevealOnScroll>
              <PlateFrame
                figNo="Figure 2"
                figTitle="CRI signal bands · published thresholds"
                source="Method note"
                confidence="Documented"
                caption="The published signal bands of the Crash Risk Index. Each is a statement about how many components are stressed, never a profit claim and never an alarm."
              >
                <div className="overflow-x-auto">
                  <table className="registry" style={{ minWidth: 480 }}>
                    <thead>
                      <tr>
                        <th>Score</th>
                        <th>Band</th>
                        <th>Meaning</th>
                      </tr>
                    </thead>
                    <tbody>
                      {criSignalBands.map((band) => (
                        <tr key={band.level}>
                          <td>{band.range}</td>
                          <td className="name">{band.level}</td>
                          <td>{band.meaning}</td>
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

        {/* S4 · How the terminal uses CRI */}
        <section id="usage" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="03" label="Usage · Corroborate and hedge" />

            <RevealOnScroll className="mb-10 max-w-[66ch]">
              <h2 className="editorial-thesis text-primary">
                What the terminal does with an elevated read.
              </h2>
              <p className="mt-5 text-secondary">
                CRI is not a chart to admire. The terminal uses it in two
                places, and both are mechanical.
              </p>
            </RevealOnScroll>

            <RevealOnScroll as="p" className="mb-8 max-w-[66ch] text-secondary">
              The first is corroboration. Radon&apos;s method runs every
              candidate trade through seven milestones, and the corroboration
              milestone cross-checks the flow read against the four regime
              models. The rule is written into the milestone: a signal fighting
              the regime is downgraded, not ignored. A bullish dark-pool
              accumulation read arriving while CRI sits in an elevated band
              keeps its evidence but loses conviction; agreement between flow
              and regime raises it. The full sequence, and{" "}
              <Link href="/convex-options-from-dark-pool-flow" className={inlineLink}>
                where corroboration sits in the method
              </Link>
              , is documented in the flow dossier.
            </RevealOnScroll>

            <RevealOnScroll as="p" className="mb-8 max-w-[66ch] text-secondary">
              The second is the hedge. The strategy registry carries a play
              named Crash Hedge: when CRI enters the elevated band, layer cheap
              tail protection through{" "}
              <Link href="/defined-risk-options-structures" className={inlineLink}>
                the put debit spread, from the catalog
              </Link>
              . The structure is defined-risk by construction: the maximum loss
              is the debit paid, known at submit. The registry sets the
              play&apos;s convexity floor at 5.0 times, the highest floor in
              the registry, because tail protection is only worth holding when
              its payoff is disproportionate to its cost. The logic is the
              insurance buyer&apos;s: buy protection while the surface still
              prices it as cheap relative to what it covers, which is precisely
              the window before the steepening finishes.
            </RevealOnScroll>

            <RevealOnScroll as="p" className="max-w-[66ch] text-secondary">
              CRI does not act alone. It is one of{" "}
              <Link href="/#regime" className={inlineLink}>
                four regime models
              </Link>
              , alongside GEX for dealer positioning, VCG-R for panic in the
              volatility-credit spread, and GRG for gamma rotation. The four
              are deliberately orthogonal. A tail-risk read that agrees with a
              widening volatility-credit gap is a different situation from a
              tail-risk read that stands alone, and the terminal treats them
              differently.
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S5 · CRI and the VIX */}
        <section id="contrast" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="04" label="Contrast · A level is not a regime" />

            <RevealOnScroll className="mb-10 max-w-[66ch]">
              <h2 className="editorial-thesis text-primary">
                A level is not a regime.
              </h2>
              <p className="mt-5 text-secondary">
                The VIX is a single number: an index of 30-day implied
                volatility on the S&amp;P 500. It answers one question well:
                what does near-the-money protection cost for the next month.
              </p>
            </RevealOnScroll>

            <RevealOnScroll as="p" className="mb-8 max-w-[66ch] text-secondary">
              CRI answers a different question. The VIX enters it as one
              component of four, scored for level and for speed, but the index
              also reads VVIX, implied correlation, and trend. A vol level can
              stay moderate while implied correlation spikes and SPX slides
              below its 100-day average. In that configuration the VIX reads
              calm and CRI does not. The reverse also happens: an elevated vol
              level while correlation stays low and the index holds its trend,
              where the market prices general uncertainty rather than a crash
              regime.
            </RevealOnScroll>

            <RevealOnScroll as="p" className="max-w-[66ch] text-secondary">
              None of this makes CRI a better number than the VIX. They are
              different instruments measuring different things, and the honest
              claim is narrower: when they disagree, the disagreement is
              information about which kind of stress the market is pricing.
              Radon reads both; it acts on the regime.
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S6 · FAQ */}
        <section id="faq" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="Appendix" label="Questions · Plain answers" />

            <RevealOnScroll className="mb-[42px] max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                Asked about the index.
              </h2>
              <p className="text-secondary">
                The questions traders bring to the Crash Risk Index, answered
                plainly: what it reads, what it is not, and what an elevated
                band actually changes.
              </p>
            </RevealOnScroll>

            <RevealOnScroll>
              <div className="border-t border-hairline-soft">
                {crashRiskIndexFaqEntries.map((entry) => (
                  <div
                    key={entry.question}
                    className="border-b border-hairline-soft py-[26px]"
                  >
                    <h3 className="mb-[7px] font-serif text-[1.32rem] font-medium tracking-[-0.01em] text-primary">
                      {entry.question}
                    </h3>
                    <p className="m-0 max-w-[66ch] text-[1.02rem] leading-[1.55] text-secondary">
                      {linkifyDemoMentions(entry.answer)}
                    </p>
                  </div>
                ))}
              </div>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        {/* S7 · Cluster CTA */}
        <section id="cta" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <RevealOnScroll className="max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                Run it against the live tape.
              </h2>
              <p className="mb-[34px] text-secondary">
                The demo instance runs the same regime models on seeded data.
                Read the CRI band yourself before you take anyone&apos;s word
                for it, including ours.
              </p>
              <div className="flex flex-wrap items-center gap-x-6 gap-y-4">
                <a
                  href={DEMO_URL}
                  className={`inline-block rounded-[4px] border border-grid px-[22px] py-[13px] font-mono text-[12px] uppercase tracking-[0.06em] text-primary transition-colors hover:border-signal-deep hover:text-signal-deep ${focusRing}`}
                >
                  Try the free demo
                </a>
                <Link
                  href="/#regime"
                  className={`font-mono text-[12px] uppercase tracking-[0.06em] text-secondary underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep ${focusRing}`}
                >
                  See all four regime models
                </Link>
              </div>
            </RevealOnScroll>
          </div>
        </section>

        <EditorialFooter />
      </main>

      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify(crashRiskIndexStructuredData()),
        }}
      />
    </div>
  );
}
