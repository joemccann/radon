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
  PAGE_NAV_LABEL,
  kellyPageMetadata,
  kellySizingFaqEntries,
  pageStructuredData,
  workedExampleRows,
} from "@/lib/pages/fractional-kelly-position-sizing";

export const metadata: Metadata = kellyPageMetadata();

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";

const inlineLink =
  "underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep";

export default function FractionalKellyPositionSizingPage() {
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
        <nav
          aria-label="Breadcrumb"
          className="border-b border-hairline-soft"
        >
          <div className="mx-auto max-w-[1140px] px-8 py-[13px] font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted">
            <Link
              href="/"
              className={`transition-colors hover:text-signal-deep ${focusRing}`}
            >
              Radon
            </Link>{" "}
            <span aria-hidden="true">/</span>{" "}
            <span className="text-signal-deep">{PAGE_NAV_LABEL}</span>
          </div>
        </nav>

        <section className="px-8 pb-[clamp(48px,6vw,80px)] pt-[clamp(56px,7vw,96px)]">
          <div className="mx-auto max-w-[1140px]">
            <RevealOnScroll initiallyShown className="mb-[26px]">
              <EditorialEyebrow>Dossier · Risk</EditorialEyebrow>
            </RevealOnScroll>
            <RevealOnScroll
              initiallyShown
              as="h1"
              className="editorial-display mb-7 max-w-[16ch] text-primary"
            >
              Position size is a policy, not a feeling.
            </RevealOnScroll>
            <RevealOnScroll
              initiallyShown
              as="p"
              className="max-w-[66ch] text-[1.22rem] leading-[1.52] text-secondary"
            >
              Of everything a trading terminal claims to do, sizing is the one
              decision it can actually enforce. Radon sizes every options
              position by fractional Kelly, computed from the structure&apos;s
              maximum loss and the signal&apos;s odds, then hard-capped at 2.5%
              of bankroll per position. The cap is a ceiling, not a target.
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        <section id="estimation" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="01" label="Kelly · The estimation problem" />
            <RevealOnScroll className="max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                Full Kelly assumes knowledge you do not have.
              </h2>
              <p className="mb-[1.05em] text-secondary">
                The Kelly criterion answers a precise question: given the odds
                of a bet and the probability of winning it, what fraction of a
                bankroll maximizes long-run growth? The formula is short. For a
                bet that pays b times the amount risked, with win probability
                p, the optimal fraction is f = p - (1 - p) / b. Nothing in the
                arithmetic is controversial. The problem is what it takes as
                given.
              </p>
              <p className="mb-[1.05em] text-secondary">
                Kelly assumes p is known. In markets it never is. Radon&apos;s
                probability estimates come from flow: dark-pool and OTC prints
                scored as accumulation or distribution, then checked against
                regime models. That is an estimate with error in it, not a
                known quantity. And full Kelly does not fail gracefully when
                the estimate runs hot. Betting past the true optimum cuts
                growth and deepens drawdowns, and betting far past it turns a
                positive-edge strategy into a losing one.
              </p>
              <p className="m-0 text-secondary">
                The asymmetry is the point. Sizing below optimal Kelly costs a
                modest amount of theoretical growth; sizing above it destroys
                capital at an accelerating rate. When the input carries error,
                the rational response is to shade down. A fraction of Kelly
                trades growth for survivability against estimation error, and
                Radon makes that trade as standing policy, decided before any
                single trade is on the screen.
              </p>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        <section id="inputs" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading
              no="02"
              label="Inputs · Odds from the structure, probability from the signal"
            />
            <div className="grid items-start gap-[clamp(36px,5vw,72px)] md:grid-cols-2">
              <RevealOnScroll>
                <h2 className="editorial-thesis mb-[22px] text-primary">
                  The formula needs two numbers. The terminal supplies both.
                </h2>
                <p className="mb-[1.05em] text-secondary">
                  The net odds b come from the structure itself. Radon routes
                  defined-risk options structures: 37 of the 58 structures in
                  its catalog carry a defined risk profile, and only those
                  clear the convexity gate. For a call debit spread the catalog
                  records maximum loss as the debit paid and maximum gain as
                  the strike width minus that debit. Divide maximum gain by
                  maximum loss and b exists as a fact of the position, not a
                  forecast. This is{" "}
                  <a
                    href="/defined-risk-options-structures"
                    className={inlineLink}
                  >
                    where max loss comes from
                  </a>
                  , and it is why defined risk comes first: an undefined loss
                  makes b, and therefore Kelly, uncomputable.
                </p>
                <p className="m-0 text-secondary">
                  The probability p comes from the signal. The flow score
                  supplies the base estimate, and the corroboration milestone
                  adjusts it: agreement between the flow read and the four
                  regime models raises conviction, while a signal fighting the
                  regime is downgraded, not ignored. The result is still an
                  estimate. That is the argument of the previous section, and
                  the reason the formula&apos;s output is treated as an input
                  to a cap rather than a command.
                </p>
              </RevealOnScroll>

              <RevealOnScroll>
                <PlateFrame
                  figNo="Exhibit 1"
                  figTitle="Fractional Kelly sizing · call debit spread · worked arithmetic"
                  source="Catalog + Gate 3"
                  confidence="Arithmetic"
                  caption="Worked example, derived line by line. The structure fixes the odds, the signal estimates the probability, and the cap has the final word."
                >
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse font-mono text-[13px]">
                      <tbody>
                        {workedExampleRows.map((row) => (
                          <tr
                            key={row.step}
                            className="border-b border-hairline-soft last:border-b-0"
                          >
                            <td className="whitespace-nowrap py-[10px] pr-6 align-top text-[10.5px] uppercase tracking-[0.1em] text-muted">
                              {row.step}
                            </td>
                            <td className="whitespace-nowrap py-[10px] text-[12.5px] tracking-[0.01em] text-primary">
                              {row.value}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </PlateFrame>
                <p className="mb-0 mt-[22px] max-w-[58ch] text-[1.02rem] leading-[1.55] text-secondary">
                  Full Kelly at 26.7% of bankroll on a single options spread is
                  the formula operating exactly as designed on an input it
                  should not trust. Quarter Kelly shades it to 6.7%. The Radon
                  cap then binds at 2.5%. Each layer between the raw formula
                  and the routed order exists to absorb the error in p.
                </p>
              </RevealOnScroll>
            </div>
          </div>
        </section>

        <SectionRule />

        <section id="cap" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="03" label="Cap · 2.5% of bankroll, per position" />
            <RevealOnScroll className="max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                A ceiling that does not move.
              </h2>
              <p className="mb-[1.05em] text-secondary">
                Even shaded, Kelly is a per-bet answer to a portfolio question.
                Three things break the per-bet frame. Positions correlate:
                several flow signals expressed in the same regime are not
                independent bets, and Kelly&apos;s arithmetic assumes they are.
                Estimates err, as the first section argued. And regimes shift,
                which changes p after entry while the position size stays where
                it was set. The hard cap is the fixed backstop against all
                three, and it holds whatever the formula reports.
              </p>
              <p className="mb-[1.05em] text-secondary">
                The policy is Gate 3 of Radon&apos;s four execution gates:
                fractional Kelly, hard cap 2.5% of bankroll per position.
                Milestone 6 of the evaluation pipeline enforces it, and the
                pipeline document describes the enforcement in four words: hard
                cap, not advisory. A structure that has not been sized under
                the cap does not reach milestone 7, and milestone 7 is the only
                place an order gets routed.
              </p>
              <p className="m-0 text-secondary">
                Conviction still matters, but it operates inside the ceiling. A
                strong signal with regime agreement sizes toward the cap; a
                downgraded signal sizes well under it. What conviction can
                never do is move the ceiling. The number is the same on the
                best setup of the quarter and the most ordinary one, which is
                what makes it a policy rather than a mood.
              </p>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        <section id="chokepoint" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="04" label="Chokepoint · Verify before route" />
            <RevealOnScroll className="max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                The cap is enforced where the order is built.
              </h2>
              <p className="mb-[1.05em] text-secondary">
                A sizing rule that lives in a document is a suggestion. Radon&apos;s
                lives in the execution path. Before any order routes, the
                mandatory pre-trade chokepoint computes maximum loss and margin
                impact on the assembled combo, exactly as it will be submitted.
                The check is not optional and cannot be bypassed. There is no
                route around it, so there is no routed order it has not seen.
              </p>
              <p className="m-0 text-secondary">
                What survives the chokepoint is journaled. The trade journal is
                the canonical store of executed positions, and the audit trail
                records the full decision chain: the signal that opened the
                evaluation, the structure that expressed it, the Kelly
                arithmetic and the cap that sized it, and the check that
                cleared it. Sizing is one step inside{" "}
                <a
                  href="/convex-options-from-dark-pool-flow"
                  className={inlineLink}
                >
                  the method around the sizing step
                </a>
                , and the journal is where the whole chain becomes inspectable
                after the fact.
              </p>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        <section id="faq" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="Appendix" label="Questions · Sizing" />
            <RevealOnScroll className="mb-[42px] max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                Sizing, answered plainly.
              </h2>
              <p className="text-secondary">
                The questions traders ask about the cap, in the order they ask
                them.
              </p>
            </RevealOnScroll>
            <RevealOnScroll>
              <div className="border-t border-hairline-soft">
                {kellySizingFaqEntries.map((entry) => (
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

        <section className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <RevealOnScroll className="border-y border-hairline-soft py-[clamp(40px,6vw,72px)]">
              <h2 className="editorial-thesis mb-[18px] text-primary">
                Run it against the live tape.
              </h2>
              <p className="mb-[30px] max-w-[60ch] text-secondary">
                The sizing policy is visible end to end in the demo: signal,
                structure, Kelly arithmetic, cap, and the pre-trade check that
                enforces it.
              </p>
              <div className="flex flex-wrap items-center gap-[26px]">
                <a
                  href={DEMO_URL}
                  className={`inline-block rounded-[4px] border border-grid px-[16px] py-[10px] font-mono text-[11px] uppercase tracking-[0.06em] text-primary transition-colors hover:border-signal-deep hover:text-signal-deep ${focusRing}`}
                >
                  Try the free demo
                </a>
                <Link
                  href="/#pipeline"
                  className={`font-mono text-[12px] uppercase tracking-[0.04em] text-secondary underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep ${focusRing}`}
                >
                  See where sizing sits in the method →
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
          __html: JSON.stringify(pageStructuredData()),
        }}
      />
    </div>
  );
}
