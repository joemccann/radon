import { clusterPages } from "./cluster-pages";
import { developerRecipes, formatAgentPrompt } from "./agent-prompts";
import {
  agentPages,
  pageToMarkdown,
  recipesPage,
} from "./developer-pages";
import {
  DEMO_URL,
  flowArgumentSteps,
  gates,
  milestones,
  regimeModels,
} from "./editorial-content";
import { faqEntries } from "./faq-content";
import { legalPages } from "./legal-pages";
import { PRIVACY_H1, PRIVACY_INTRO, privacySections } from "./pages/privacy";
import { TERMS_H1, TERMS_INTRO, termsSections } from "./pages/terms";
import {
  NOT_FOUND_RECOVERY_LINKS,
  notFoundMarkdown,
} from "./not-found-recovery";
import { SITE_DESCRIPTION, SITE_NAME, siteUrl } from "./seo";

export const MARKDOWN_CONTENT_TYPE = "text/markdown; charset=utf-8";
export const MARKDOWN_VARY = "Accept, Accept-Encoding";

export { NOT_FOUND_RECOVERY_LINKS, notFoundMarkdown };

function joinBlocks(blocks: string[]): string {
  return blocks.filter(Boolean).join("\n\n") + "\n";
}

function homeMarkdown(): string {
  const flow = flowArgumentSteps
    .map((step) => `- **${step.stage}:** ${step.body}`)
    .join("\n");
  const gateList = gates
    .map((gate) => {
      const state = gate.disabled ? " (disabled)" : "";
      return `- **${gate.no} ${gate.name}${state}:** ${gate.body} Rule: ${gate.rule}.`;
    })
    .join("\n");
  const regimes = regimeModels
    .map((model) => `- **${model.code} (${model.name}):** ${model.method}`)
    .join("\n");
  const pipeline = milestones
    .map((milestone, index) => `${index + 1}. **${milestone.name}:** ${milestone.body}`)
    .join("\n");
  const dossiers = clusterPages
    .map((page) => `- [${page.navLabel}](${siteUrl}/${page.slug}): ${page.description}`)
    .join("\n");
  const faq = faqEntries
    .map((entry) => `### ${entry.question}\n\n${entry.answer}`)
    .join("\n\n");

  return joinBlocks([
    `# ${SITE_NAME}`,
    SITE_DESCRIPTION,
    `Free demo: ${DEMO_URL}`,
    "## Flow",
    flow,
    "## Gates",
    gateList,
    "## Regime models",
    regimes,
    "## Method",
    pipeline,
    "## FAQ",
    faq,
    "## Dossiers",
    dossiers,
    "## Next",
    NOT_FOUND_RECOVERY_LINKS.filter((link) => link.href !== siteUrl)
      .map((link) => `- [${link.label}](${link.href}): ${link.note}`)
      .join("\n"),
  ]);
}

function legalMarkdown(
  heading: string,
  intro: string,
  sections: { heading: string; paragraphs: string[] }[],
): string {
  const body = sections
    .map(
      (section) =>
        `## ${section.heading}\n\n${section.paragraphs.join("\n\n")}`,
    )
    .join("\n\n");
  return joinBlocks([`# ${heading}`, intro, body]);
}

function statusMarkdown(): string {
  return joinBlocks([
    "# Where Radon runs in public",
    "There is no Edge replica of the production Turso store. Account figures stay on the authenticated terminal. Public product experience is the free demo with synthetic data.",
    "## Surfaces",
    [
      `- [Marketing site](${siteUrl}): editorial content only. No portfolio, journal, or account figures.`,
      `- [Public demo](${DEMO_URL}): synthetic positions and paper fills. Separate database from production.`,
      "- [Operator terminal](https://app.radon.run): live IB, Turso journal, service health. Authenticated allowlist only.",
    ].join("\n"),
  ]);
}

function clusterMarkdown(slug: string, title: string, description: string): string {
  return joinBlocks([
    `# ${title.replace(/ \| Radon Terminal$/, "")}`,
    description,
    `Canonical URL: ${siteUrl}/${slug}`,
    `Markdown: send Accept: text/markdown or open ${siteUrl}/${slug}.md`,
    "## Next",
    `- [${SITE_NAME}](${siteUrl})`,
    `- [When to use Radon Terminal](${siteUrl}/agent-instructions)`,
    `- [Radon Terminal developer resources](${siteUrl}/developers)`,
  ]);
}

const pages = new Map<string, string>();

pages.set("/", homeMarkdown());
pages.set("/status", statusMarkdown());
pages.set(
  "/privacy",
  legalMarkdown(PRIVACY_H1, PRIVACY_INTRO, privacySections),
);
pages.set("/terms", legalMarkdown(TERMS_H1, TERMS_INTRO, termsSections));

for (const page of clusterPages) {
  pages.set(`/${page.slug}`, clusterMarkdown(page.slug, page.title, page.description));
}

for (const page of agentPages) {
  pages.set(`/${page.slug}`, pageToMarkdown(page));
}

pages.set(
  `/${recipesPage.slug}`,
  joinBlocks([
    pageToMarkdown(recipesPage).trim(),
    "## Recipes",
    developerRecipes
      .map(
        (recipe) =>
          `### ${recipe.title}\n\n${formatAgentPrompt(recipe.prompt).trim()}`,
      )
      .join("\n\n"),
  ]),
);

export const MARKDOWN_PATHS = [...pages.keys()].sort();

export function lookupMarkdown(pathname: string): string | null {
  const trimmed =
    pathname !== "/" && pathname.endsWith("/")
      ? pathname.slice(0, -1)
      : pathname;
  return pages.get(trimmed) ?? null;
}

export function markdownForSlug(slug: string[] | undefined): {
  status: number;
  body: string;
} {
  const pathname = !slug || slug.length === 0 ? "/" : `/${slug.join("/")}`;
  const body = lookupMarkdown(pathname);
  if (body) {
    return { status: 200, body };
  }
  return { status: 404, body: notFoundMarkdown() };
}
