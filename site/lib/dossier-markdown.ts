import { clusterPages, type ClusterPage } from "./cluster-pages";
import { DEMO_URL } from "./editorial-content";
import {
  confidenceBands,
  faqEntries as convexFaqEntries,
  gateRows,
  interpolationFormulas,
  interpolationRules,
  milestoneDetails,
  playRows,
  signalBands,
} from "./pages/convex-options-from-dark-pool-flow";
import {
  crashRiskIndexFaqEntries,
  criComponents,
  criSignalBands,
} from "./pages/crash-risk-index";
import {
  catalog,
  catalogCounts,
  faqEntries as catalogFaqEntries,
  formatBias,
  formatLegs,
  guardLadder,
  registryStructureMap,
  structureFamilies,
} from "./pages/defined-risk-options-structures";
import {
  kellySizingFaqEntries,
  workedExampleRows,
} from "./pages/fractional-kelly-position-sizing";
import {
  faqEntries as ibFaqEntries,
  ibRoles,
  requirementRows as ibRequirementRows,
} from "./pages/interactive-brokers-dark-pool-terminal";
import {
  faqEntries as uwFaqEntries,
  ingestSurfaces,
  pipelineStages,
  requirementRows as uwRequirementRows,
} from "./pages/unusual-whales-interactive-brokers";
import { SITE_NAME, siteUrl } from "./seo";

type FaqEntry = {
  question: string;
  answer: string;
};

type TableCell = string | number | boolean | null | undefined;

function joinBlocks(blocks: string[]): string {
  return blocks.filter(Boolean).join("\n\n") + "\n";
}

export function escapeMarkdownTableCell(value: TableCell): string {
  return String(value ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/\|/g, "\\|")
    .replace(/\r?\n/g, "<br>");
}

export function markdownTable(headers: string[], rows: TableCell[][]): string {
  const row = (cells: TableCell[]) =>
    `| ${cells.map(escapeMarkdownTableCell).join(" | ")} |`;
  return [
    row(headers),
    row(headers.map(() => "---")),
    ...rows.map(row),
  ].join("\n");
}

function faqMarkdown(entries: FaqEntry[]): string {
  return entries
    .map((entry) => `### ${entry.question}\n\n${entry.answer}`)
    .join("\n\n");
}

function dossierHeader(page: ClusterPage): string[] {
  return [
    `# ${page.title.replace(/ \| Radon Terminal$/, "")}`,
    page.description,
    `Canonical URL: ${siteUrl}/${page.slug}`,
    `Markdown URL: ${siteUrl}/${page.slug}.md`,
    `Last updated: ${page.lastModified}`,
  ];
}

function resourcesMarkdown(currentSlug: string): string {
  const dossiers = clusterPages
    .filter((page) => page.slug !== currentSlug)
    .map(
      (page) =>
        `- [${page.navLabel}](${siteUrl}/${page.slug}): ${page.description}`,
    );
  return [
    `- [${SITE_NAME}](${siteUrl})`,
    `- [Free demo](${DEMO_URL})`,
    `- [When to use Radon Terminal](${siteUrl}/agent-instructions)`,
    `- [Radon Terminal developer resources](${siteUrl}/developers)`,
    ...dossiers,
  ].join("\n");
}

function interactiveBrokersMarkdown(page: ClusterPage): string {
  return joinBlocks([
    ...dossierHeader(page),
    "## Interactive Brokers roles",
    markdownTable(
      ["Role", "Interactive Brokers supplies", "Terminal use"],
      ibRoles.map((role) => [role.role, role.supplies, role.terminalUse]),
    ),
    "## Live and demo requirements",
    markdownTable(
      ["Item", "Live use", "Demo"],
      ibRequirementRows.map((row) => [row.item, row.liveUse, row.demo]),
    ),
    "## Questions",
    faqMarkdown(ibFaqEntries),
    "## Resources",
    resourcesMarkdown(page.slug),
  ]);
}

function structureCatalogMarkdown(page: ClusterPage): string {
  const families = structureFamilies
    .map((family) =>
      [
        `### ${family.label}`,
        markdownTable(
          [
            "Structure",
            "Aliases",
            "Legs",
            "Bias",
            "Risk profile",
            "Max gain",
            "Max loss",
            "Guard decision",
            "Guard reason",
            "Guard gap",
            "Notes",
          ],
          family.structures.map((structure) => [
            structure.name,
            structure.aliases.join(", "),
            formatLegs(structure),
            formatBias(structure.bias),
            structure.risk_profile,
            structure.max_gain,
            structure.max_loss,
            structure.guard_decision,
            structure.guard_reason,
            structure.guard_gap,
            structure.notes,
          ]),
        ),
      ].join("\n\n"),
    )
    .join("\n\n");

  return joinBlocks([
    ...dossierHeader(page),
    "## Catalog summary",
    [
      `- Total structures: ${catalogCounts.total}`,
      `- Defined risk: ${catalogCounts.defined}`,
      `- Undefined risk: ${catalogCounts.undefinedRisk}`,
      `- Conditional risk: ${catalogCounts.conditional}`,
      `- Guard allows: ${catalogCounts.allowed}`,
      `- Guard blocks: ${catalogCounts.blocked}`,
      `- Undefined-risk structures allowed by the guard: ${catalogCounts.undefinedAllowed}`,
      `- Structure families: ${catalogCounts.families}`,
    ].join("\n"),
    "## Guard decision ladder",
    markdownTable(
      ["Rule", "Decision", "Note"],
      guardLadder.map((rule) => [rule.rule, rule.decision, rule.note]),
    ),
    `## Full structure catalog (${catalog.length} structures)`,
    families,
    "## Registry play mapping",
    markdownTable(
      ["Play", "Structure", "Family"],
      registryStructureMap.map((mapping) => [
        mapping.play,
        mapping.structure,
        mapping.family,
      ]),
    ),
    "## Questions",
    faqMarkdown(catalogFaqEntries),
    "## Resources",
    resourcesMarkdown(page.slug),
  ]);
}

function fractionalKellyMarkdown(page: ClusterPage): string {
  return joinBlocks([
    ...dossierHeader(page),
    "## Structure, payoff, and signal odds",
    markdownTable(
      ["Step", "Value"],
      workedExampleRows.slice(0, 6).map((row) => [row.step, row.value]),
    ),
    "## Fractional Kelly and the hard cap",
    markdownTable(
      ["Step", "Value"],
      workedExampleRows.slice(6).map((row) => [row.step, row.value]),
    ),
    "## Questions",
    faqMarkdown(kellySizingFaqEntries),
    "## Resources",
    resourcesMarkdown(page.slug),
  ]);
}

function unusualWhalesMarkdown(page: ClusterPage): string {
  return joinBlocks([
    ...dossierHeader(page),
    "## Unusual Whales ingest surfaces",
    ingestSurfaces
      .map((surface) => `### ${surface.label}\n\n${surface.body}`)
      .join("\n\n"),
    "## Print-to-order pipeline",
    pipelineStages
      .map(
        (stage, index) =>
          `${index + 1}. **${stage.name}:** ${stage.body} (${stage.tag})`,
      )
      .join("\n"),
    "## Live and demo requirements",
    markdownTable(
      ["Component", "Supplies", "Required for"],
      uwRequirementRows.map((row) => [
        row.component,
        row.supplies,
        row.requiredFor,
      ]),
    ),
    "## Questions",
    faqMarkdown(uwFaqEntries),
    "## Resources",
    resourcesMarkdown(page.slug),
  ]);
}

function convexOptionsMarkdown(page: ClusterPage): string {
  const milestones = milestoneDetails
    .map((milestone, index) => {
      const reference = milestone.reference
        ? ` [${milestone.reference.label}](${siteUrl}${milestone.reference.href})`
        : "";
      return `${index + 1}. **${milestone.name}:** ${milestone.body} (${milestone.tag})${reference}`;
    })
    .join("\n");
  const bands = signalBands.flatMap((signal) =>
    signal.bands.map((band) => [signal.signal, band.range, band.read]),
  );

  return joinBlocks([
    ...dossierHeader(page),
    "## Sequential gates",
    markdownTable(
      ["Gate", "Name", "Rule", "Condition", "State"],
      gateRows.map((gate) => [
        gate.no,
        gate.name,
        gate.rule,
        gate.condition,
        gate.disabled ? "Disabled" : "Active",
      ]),
    ),
    "## Seven milestones",
    milestones,
    "## Intraday interpolation",
    markdownTable(
      ["Term", "Expression"],
      interpolationFormulas.map((formula) => [
        formula.label,
        formula.expression,
      ]),
    ),
    markdownTable(
      ["Session progress", "Confidence", "Prior weight"],
      confidenceBands.map((band) => [
        band.progress,
        band.confidence,
        band.priorWeight,
      ]),
    ),
    interpolationRules.map((rule) => `- ${rule}`).join("\n"),
    "## Signal interpretation",
    markdownTable(["Signal", "Range", "Read"], bands),
    "## Strategy registry",
    markdownTable(
      ["Play", "Edge", "Structure", "Convexity floor"],
      playRows.map((play) => [
        play.name,
        play.edge,
        play.structure,
        play.convexityFloor,
      ]),
    ),
    "## Questions",
    faqMarkdown(convexFaqEntries),
    "## Resources",
    resourcesMarkdown(page.slug),
  ]);
}

function crashRiskIndexMarkdown(page: ClusterPage): string {
  return joinBlocks([
    ...dossierHeader(page),
    "## Published components",
    markdownTable(
      ["Component", "Weight", "Inputs", "Read when it scores"],
      criComponents.map((component) => [
        component.name,
        component.weight,
        component.inputs,
        component.read,
      ]),
    ),
    "## Signal bands",
    markdownTable(
      ["Score", "Band", "Meaning"],
      criSignalBands.map((band) => [band.range, band.level, band.meaning]),
    ),
    "## Questions",
    faqMarkdown(crashRiskIndexFaqEntries),
    "## Resources",
    resourcesMarkdown(page.slug),
  ]);
}

const builders: Record<string, (page: ClusterPage) => string> = {
  "interactive-brokers-dark-pool-terminal": interactiveBrokersMarkdown,
  "defined-risk-options-structures": structureCatalogMarkdown,
  "fractional-kelly-position-sizing": fractionalKellyMarkdown,
  "unusual-whales-interactive-brokers": unusualWhalesMarkdown,
  "convex-options-from-dark-pool-flow": convexOptionsMarkdown,
  "crash-risk-index": crashRiskIndexMarkdown,
};

export const dossierMarkdownBySlug = new Map(
  clusterPages.map((page) => {
    const build = builders[page.slug];
    if (!build) {
      throw new Error(`No dossier Markdown builder for ${page.slug}`);
    }
    return [page.slug, build(page)] as const;
  }),
);

export function dossierMarkdown(slug: string): string | null {
  return dossierMarkdownBySlug.get(slug) ?? null;
}
