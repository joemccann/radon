import type { Metadata } from "next";
import Link from "next/link";
import { EditorialEyebrow } from "@/components/atoms/EditorialEyebrow";
import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { ScrollProgress } from "@/components/atoms/ScrollProgress";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { SectionRule } from "@/components/atoms/SectionRule";
import { SignalPill } from "@/components/atoms/SignalPill";
import { PlateFrame } from "@/components/molecules/PlateFrame";
import { EditorialFooter } from "@/components/sections/EditorialFooter";
import { EditorialHeader } from "@/components/sections/EditorialHeader";
import { DEMO_URL } from "@/lib/editorial-content";
import {
  catalogCounts,
  faqEntries,
  formatBias,
  formatLegs,
  guardLadder,
  H1_TEXT,
  META_DESCRIPTION,
  NAV_LABEL,
  PAGE_TITLE,
  pageStructuredData,
  registryStructureMap,
  riskVerdict,
  SLUG,
  structureFamilies,
  undefinedAllowedStructures,
  type CatalogStructure,
  type RiskVerdict,
} from "@/lib/pages/defined-risk-options-structures";
import { SITE_NAME, SOCIAL_IMAGE_ALT, SOCIAL_IMAGE_PATH } from "@/lib/seo";

// TODO(integrator): replace with clusterPageMetadata({ slug, title, description })
// from @/lib/seo once the shared helper lands. The shape below matches the
// cluster contract exactly (openGraph re-includes the shared image because
// Next's metadata merge is shallow).
export const metadata: Metadata = {
  title: PAGE_TITLE,
  description: META_DESCRIPTION,
  alternates: { canonical: `/${SLUG}` },
  openGraph: {
    type: "article",
    url: `/${SLUG}`,
    title: PAGE_TITLE,
    description: META_DESCRIPTION,
    siteName: SITE_NAME,
    locale: "en_US",
    images: [
      {
        url: SOCIAL_IMAGE_PATH,
        width: 1200,
        height: 630,
        alt: SOCIAL_IMAGE_ALT,
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: PAGE_TITLE,
    description: META_DESCRIPTION,
    images: [SOCIAL_IMAGE_PATH],
  },
};

const inlineLink =
  "underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep";

const riskTone: Record<RiskVerdict, "emerging" | "fault" | "warn"> = {
  defined: "emerging",
  undefined: "fault",
  conditional: "warn",
};

function familyLabel(id: string): string {
  return structureFamilies.find((family) => family.id === id)?.label ?? id;
}

function GuardDecisionCell({ structure }: { structure: CatalogStructure }) {
  if (structure.guard_decision === "BLOCK") {
    return <span className="text-negative">BLOCK</span>;
  }
  return <span className="text-signal-deep">ALLOW</span>;
}

function StructureFamilyTable({
  label,
  structures,
}: {
  label: string;
  structures: CatalogStructure[];
}) {
  return (
    <div className="mb-10 last:mb-0">
      <h3 className="mb-3 flex flex-wrap items-baseline gap-3 font-serif text-[1.32rem] font-medium tracking-[-0.01em] text-primary">
        {label}
        <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-muted">
          {structures.length} {structures.length === 1 ? "entry" : "entries"}
        </span>
      </h3>
      <div className="overflow-x-auto">
        <table className="scan-table min-w-[980px]">
          <thead>
            <tr>
              <th>Structure</th>
              <th>Legs</th>
              <th>Bias</th>
              <th>Risk</th>
              <th>Max gain</th>
              <th>Max loss</th>
              <th className="text-right">Guard</th>
            </tr>
          </thead>
          <tbody>
            {structures.map((structure) => {
              const verdict = riskVerdict(structure);
              return (
                <tr key={structure.name}>
                  <td className="tk">
                    {structure.name}
                    {structure.aliases.length > 0 && (
                      <span className="mt-[2px] block text-[10.5px] text-muted">
                        {structure.aliases.join(" · ")}
                      </span>
                    )}
                  </td>
                  <td>{formatLegs(structure)}</td>
                  <td>{formatBias(structure.bias)}</td>
                  <td>
                    <SignalPill tone={riskTone[verdict]}>{verdict}</SignalPill>
                  </td>
                  <td>{structure.max_gain}</td>
                  <td>{structure.max_loss}</td>
                  <td className="num">
                    <GuardDecisionCell structure={structure} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function DefinedRiskOptionsStructuresPage() {
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
        {/* TODO(integrator): swap for the shared ClusterBreadcrumb molecule.
            Markup mirrors the BreadcrumbList JSON-LD exactly per the contract. */}
        <div className="border-b border-hairline-soft">
          <nav
            aria-label="Breadcrumb"
            className="mx-auto max-w-[1140px] px-8 py-[13px] font-mono text-[11.5px] uppercase tracking-[0.14em] text-muted"
          >
            <Link href="/" className="transition-colors hover:text-signal-deep">
              Radon
            </Link>{" "}
            <span aria-hidden>/</span>{" "}
            <span className="text-signal-deep">{NAV_LABEL}</span>
          </nav>
        </div>

        <section
          id="top"
          className="px-8 pb-[clamp(48px,6vw,80px)] pt-[clamp(56px,7vw,96px)]"
        >
          <div className="mx-auto max-w-[1140px]">
            <RevealOnScroll initiallyShown className="mb-[26px]">
              <EditorialEyebrow>Dossier · Reference</EditorialEyebrow>
            </RevealOnScroll>
            <RevealOnScroll
              initiallyShown
              as="h1"
              className="editorial-display mb-7 max-w-[18ch] text-primary"
            >
              {H1_TEXT}
            </RevealOnScroll>
            <RevealOnScroll
              initiallyShown
              as="p"
              className="max-w-[66ch] text-[1.22rem] leading-[1.52] text-secondary"
            >
              A structure catalog is a risk policy, not a menu. Radon’s
              canonical catalog holds {catalogCounts.total} options structures
              across {catalogCounts.families} families and states, for each
              one, the legs, the bias, the maximum gain, and the maximum loss.{" "}
              {catalogCounts.defined} carry defined risk,{" "}
              {catalogCounts.undefinedRisk} carry undefined risk, and{" "}
              {catalogCounts.conditional} are conditional: defined or undefined
              depending on how strikes and tenors are set.
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        <section id="definition" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="01" label="Definition · Loss known at submit" />
            <RevealOnScroll className="mb-[34px]">
              <h2 className="editorial-thesis text-primary">
                Defined risk has a denominator.
              </h2>
            </RevealOnScroll>
            <RevealOnScroll className="max-w-[66ch] space-y-6 text-secondary">
              <p>
                Defined risk has a narrow, mechanical meaning here: the maximum
                loss of a position is computable before the order exists, from
                the structure alone. A call debit spread can lose the debit
                paid and nothing more. A credit spread can lose the strike
                width minus the credit received. A long option can lose its
                premium. None of these numbers depend on a forecast, a
                volatility model, or an exit executed in time. They are
                properties of the legs.
              </p>
              <p>
                That property is the precondition for Gate 1, Radon’s
                convexity gate. The gate requires gain of at least 2 times
                loss, on defined-risk structures only, and a ratio needs a
                denominator. If the maximum loss is unlimited, or contingent on
                where the underlying opens after a gap, gain over loss is not a
                number that can be checked at submit. The gate would be reduced
                to an opinion.
              </p>
              <p>
                The same denominator drives sizing. Radon sizes positions with
                fractional Kelly under a hard cap of 2.5 percent of bankroll
                per position, and Kelly arithmetic starts from the amount at
                risk. A structure without a computable max loss cannot be
                sized, only guessed at.{" "}
                <Link
                  href="/fractional-kelly-position-sizing"
                  className={inlineLink}
                >
                  Why defined loss makes sizing computable
                </Link>{" "}
                is the subject of its own dossier.
              </p>
              <p>
                The catalog’s edge case makes the definition precise. The
                Short Put (Cash-Secured) entry is marked defined with a max
                loss of strike × 100, and its notes record the collateral
                requirement: strike × 100 per contract, cash that covers
                assignment. The worst case, assignment with the stock at zero,
                is a large number. It is also a known number. Defined risk
                means known loss, not small loss.
              </p>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        <section id="catalog" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading
              no="02"
              label={`Catalog · ${catalogCounts.total} entries, ${catalogCounts.families} families`}
            />
            <RevealOnScroll className="mb-[34px]">
              <h2 className="editorial-thesis text-primary">
                The full taxonomy, as the software reads it.
              </h2>
            </RevealOnScroll>
            <RevealOnScroll className="mb-[42px] max-w-[66ch] space-y-6 text-secondary">
              <p>
                The tables below render the actual machine-readable catalog,
                not a summary of it. The same JSON file drives the order-entry
                guard in the web app and the post-sync audit script, so what
                you read here is what the software checks. Every count on this
                page is recomputed from that file at build time.
              </p>
              <p>
                Read each row as a contract. Legs are stated in guard notation:
                action, type, strike role, and tenor where it matters. Max gain
                and max loss are formulas, not forecasts; where the catalog
                writes debit paid or width minus credit, that is the entire
                claim. The risk verdict is the catalog’s own field, and the
                guard column is the decision the leg-matching logic returns for
                that structure.
              </p>
            </RevealOnScroll>
            <RevealOnScroll>
              <PlateFrame
                figNo="Table 1"
                figTitle={`The structure catalog · ${catalogCounts.total} entries · ${catalogCounts.families} families`}
                source="options-structures.json"
                confidence="Canonical"
                caption={`The catalog that Gate 4's guard logic evaluates. ${catalogCounts.defined} defined, ${catalogCounts.undefinedRisk} undefined, ${catalogCounts.conditional} conditional; ${catalogCounts.allowed} allowed and ${catalogCounts.blocked} blocked by the guard, including ${catalogCounts.undefinedAllowed} allows whose risk profile is undefined.`}
              >
                <div>
                  {structureFamilies.map((family) => (
                    <StructureFamilyTable
                      key={family.id}
                      label={family.label}
                      structures={family.structures}
                    />
                  ))}
                </div>
              </PlateFrame>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        <section id="guard" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="03" label="Guard · Decision logic, honestly" />
            <RevealOnScroll className="mb-[34px]">
              <h2 className="editorial-thesis text-primary">
                The guard, with its limits on the record.
              </h2>
            </RevealOnScroll>
            <RevealOnScroll className="mb-[42px] max-w-[66ch] space-y-6 text-secondary">
              <p>
                The guard is not a risk model. It is a leg-matching ladder,
                evaluated top down: anything bought is allowed, a sold put is
                allowed because cash secures it, a combo holding both long and
                short legs is allowed on the assumption the long leg covers,
                and a sold call or sold stock without coverage is blocked. The
                ladder is simple on purpose, and the catalog documents exactly
                where simple is not enough.
              </p>
            </RevealOnScroll>
            <RevealOnScroll className="mb-[42px]">
              <PlateFrame
                figNo="Table 2"
                figTitle="Guard decision ladder · evaluated top down"
                source="options-structures.md"
                confidence="Canonical"
                caption="The full decision ladder from the catalog reference. One rule in the middle is itself marked as a gap: a synthetic short reads as a hedged combo to leg-matching logic while carrying a naked call."
              >
                <div className="overflow-x-auto">
                  <table className="scan-table min-w-[640px]">
                    <thead>
                      <tr>
                        <th>Order shape</th>
                        <th>Decision</th>
                        <th>Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {guardLadder.map((rule) => (
                        <tr key={rule.rule}>
                          <td className="tk">{rule.rule}</td>
                          <td>
                            {rule.decision === "BLOCK" ? (
                              <span className="text-negative">BLOCK</span>
                            ) : rule.decision === "GAP" ? (
                              <span className="text-warn">GAP</span>
                            ) : (
                              <span className="text-signal-deep">ALLOW</span>
                            )}
                          </td>
                          <td>{rule.note}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </PlateFrame>
            </RevealOnScroll>
            <RevealOnScroll className="mb-[42px] max-w-[66ch] space-y-6 text-secondary">
              <p>
                Run against the full catalog, the ladder allows{" "}
                {catalogCounts.allowed} structures and blocks{" "}
                {catalogCounts.blocked}. {catalogCounts.undefinedAllowed} of
                those allows carry an undefined risk profile. The ladder
                clears them on a convention: every sold put is treated as
                cash-secured, and long stock is its own collateral. The
                catalog reference singles out three of them, the Long Risk
                Reversal, the Jade Lizard, and the Seagull Spread, as the hard
                cases where detecting both BUY and SELL legs is not the same
                as verifying that every short leg is covered at every price
                the underlying can reach. All{" "}
                {catalogCounts.undefinedAllowed} are listed below with the
                catalog’s own notes, unedited.
              </p>
            </RevealOnScroll>
            <RevealOnScroll className="mb-[42px]">
              <PlateFrame
                figNo="Exhibit 3"
                figTitle={`Allowed with undefined risk · ${catalogCounts.undefinedAllowed} of ${catalogCounts.allowed} allows`}
                source="options-structures.json"
                confidence="Canonical"
                caption="Where a leg-matching convention meets an undefined payoff, stated in the catalog's own notes. Publishing the assumptions is the point: a guard whose conventions are named can be audited; one whose conventions are hidden cannot."
              >
                <div className="border-t border-hairline-soft">
                  {undefinedAllowedStructures.map((structure) => (
                    <div
                      key={structure.name}
                      className="border-b border-hairline-soft py-[22px]"
                    >
                      <p className="m-0 mb-[6px] font-mono text-[12px] uppercase tracking-[0.1em] text-primary">
                        {structure.name}{" "}
                        <span className="text-warn">
                          · ALLOW · risk undefined
                        </span>
                      </p>
                      <p className="m-0 mb-[10px] font-mono text-[11.5px] text-muted">
                        {formatLegs(structure)} · max loss:{" "}
                        {structure.max_loss}
                      </p>
                      <p className="m-0 max-w-[72ch] font-serif text-[15px] italic leading-[1.5] text-secondary">
                        {structure.notes}
                      </p>
                    </div>
                  ))}
                </div>
              </PlateFrame>
            </RevealOnScroll>
            <RevealOnScroll className="max-w-[66ch] space-y-6 text-secondary">
              <p>
                Two disclosures close the ledger. First, an ALLOW from the
                guard is not a pass through the gates: Gate 1 accepts
                defined-risk structures only, so an undefined risk profile
                stops a trade at convexity regardless of what the leg-matching
                ladder concluded. Second, Gate 4, the no-naked-shorts gate
                that consumes these guard decisions, is currently disabled by
                operator policy, with the blocking logic preserved for
                re-enable. The catalog keeps stating ALLOW and BLOCK either
                way: the taxonomy records what the logic decides, not whether
                the operator currently lets it act.
              </p>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        <section id="usage" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading
              no="04"
              label="Usage · Structure selection at milestone 5"
            />
            <RevealOnScroll className="mb-[34px]">
              <h2 className="editorial-thesis text-primary">
                Milestone 5 selects from this catalog.
              </h2>
            </RevealOnScroll>
            <RevealOnScroll className="mb-[42px] max-w-[66ch] space-y-6 text-secondary">
              <p>
                Radon’s evaluation pipeline runs seven milestones from ticker
                to logged trade, and milestone 5 is where the catalog gets
                used: select a structure that expresses the flow thesis with
                convexity. The milestone carries a stop condition, not a
                preference. Reward-to-risk below 2 to 1 ends the evaluation:
                no structure, no Kelly, no order. Because a milestone-5
                structure must satisfy Gate 1 by construction, selection
                happens inside the defined-risk subset of this catalog.
              </p>
              <p>
                The plays in Radon’s registry each name their structure, and
                each maps to a catalog family. The mapping below is the whole
                registry, not a selection.
              </p>
            </RevealOnScroll>
            <RevealOnScroll className="mb-[42px]">
              <PlateFrame
                figNo="Table 4"
                figTitle="Registry plays · catalog structures they select"
                source="Registry"
                confidence="Canonical"
                caption="Each registry play resolves to a defined-risk structure at milestone 5. The play supplies the thesis; the catalog supplies the payoff geometry and the max-loss denominator."
              >
                <div className="overflow-x-auto">
                  <table className="scan-table min-w-[560px]">
                    <thead>
                      <tr>
                        <th>Play</th>
                        <th>Structure</th>
                        <th>Catalog family</th>
                      </tr>
                    </thead>
                    <tbody>
                      {registryStructureMap.map((mapping) => (
                        <tr key={mapping.play}>
                          <td className="tk">{mapping.play}</td>
                          <td>{mapping.structure}</td>
                          <td>{familyLabel(mapping.family)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </PlateFrame>
            </RevealOnScroll>
            <RevealOnScroll className="max-w-[66ch] space-y-6 text-secondary">
              <p>
                Structure selection is one step in a longer chain: flow signal,
                regime corroboration, level mapping, then structure, size,
                verification, and routing.{" "}
                <Link
                  href="/convex-options-from-dark-pool-flow"
                  className={inlineLink}
                >
                  Where structure selection sits in the method
                </Link>{" "}
                is covered in the flow dossier, from dark-pool print to routed
                order.
              </p>
            </RevealOnScroll>
          </div>
        </section>

        <SectionRule />

        <section id="faq" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <SectionHeading no="05" label="Questions · Plain answers" />
            <RevealOnScroll className="mb-[42px] max-w-[66ch]">
              <h2 className="editorial-thesis mb-[22px] text-primary">
                Asked about the catalog.
              </h2>
              <p className="text-secondary">
                What defined risk does and does not mean, why the rejected
                structures are documented alongside the accepted ones, and the
                current status of the gate that consumes the guard decisions.
              </p>
            </RevealOnScroll>
            {/* TODO(integrator): swap for the generalized FaqSection once it
                accepts an entries prop. Row markup matches it exactly. */}
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

        {/* TODO(integrator): swap for the shared ClusterCta organism. */}
        <section id="cta" className="px-8 py-[clamp(64px,9vw,128px)]">
          <div className="mx-auto max-w-[1140px]">
            <RevealOnScroll>
              <h2 className="editorial-thesis mb-[18px] text-primary">
                Run it against the live tape.
              </h2>
              <p className="mb-9 max-w-[58ch] text-secondary">
                The free demo shows the scanner, the regime models, and the
                gate discipline this catalog feeds, on seeded data with no
                brokerage connection.
              </p>
              <div className="flex flex-wrap items-center gap-7">
                <a
                  href={DEMO_URL}
                  className="inline-block rounded-[4px] border border-grid px-[18px] py-[11px] font-mono text-[12px] uppercase tracking-[0.06em] text-primary transition-colors hover:border-signal-deep hover:text-signal-deep"
                >
                  Try the free demo
                </a>
                <Link
                  href="/#registry"
                  className="font-mono text-[12px] uppercase tracking-[0.06em] text-secondary underline decoration-grid underline-offset-4 transition-colors hover:text-signal-deep hover:decoration-signal-deep"
                >
                  See the plays built from these structures →
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
