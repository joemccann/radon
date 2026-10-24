import { describe, expect, it } from "vitest";
import { clusterPages } from "./cluster-pages";
import { agentPages } from "./developer-pages";
import { legalPages } from "./legal-pages";
import {
  faqEntries as convexFaqEntries,
  gateRows,
  milestoneDetails,
  playRows,
} from "./pages/convex-options-from-dark-pool-flow";
import {
  crashRiskIndexFaqEntries,
  criComponents,
  criSignalBands,
} from "./pages/crash-risk-index";
import {
  catalog,
  faqEntries as catalogFaqEntries,
  formatLegs,
  guardLadder,
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
import {
  lookupMarkdown,
  MARKDOWN_CONTENT_TYPE,
  MARKDOWN_PATHS,
  MARKDOWN_VARY,
  markdownForSlug,
  NOT_FOUND_RECOVERY_LINKS,
  notFoundMarkdown,
} from "./markdown-pages";
import { siteUrl } from "./seo";

describe("markdown page registry", () => {
  it("serves markdown for the homepage, dossiers, legal, status, and agent pages", () => {
    expect(lookupMarkdown("/")?.startsWith("# Radon Terminal")).toBe(true);
    expect(lookupMarkdown("/status")).toContain("Where Radon runs in public");
    for (const page of clusterPages) {
      expect(lookupMarkdown(`/${page.slug}`)).toContain(page.description);
    }
    for (const page of legalPages) {
      expect(lookupMarkdown(`/${page.slug}`)).toMatch(/^# /);
    }
    for (const page of agentPages) {
      const body = lookupMarkdown(`/${page.slug}`);
      expect(body).toContain(`# ${page.heading}`);
      expect(body).toContain("Radon");
    }
  });

  it("publishes substantive dossier mirrors from the structured page content", () => {
    for (const page of clusterPages) {
      const body = lookupMarkdown(`/${page.slug}`);
      expect(body).not.toBeNull();
      expect(body).toContain(`# ${page.title.replace(/ \| Radon Terminal$/, "")}`);
      expect(body).toContain(page.description);
      expect(body).toContain(`Canonical URL: ${siteUrl}/${page.slug}`);
      expect(body).toContain(`Markdown URL: ${siteUrl}/${page.slug}.md`);
      expect(body).toContain(`Last updated: ${page.lastModified}`);
      expect(body?.match(/^## /gm)?.length ?? 0).toBeGreaterThanOrEqual(3);
      expect(MARKDOWN_PATHS).toContain(`/${page.slug}`);
    }

    const ib = lookupMarkdown("/interactive-brokers-dark-pool-terminal") ?? "";
    for (const role of ibRoles) {
      expect(ib).toContain(role.role);
      expect(ib).toContain(role.supplies);
      expect(ib).toContain(role.terminalUse);
    }
    for (const requirement of ibRequirementRows) {
      expect(ib).toContain(requirement.item);
      expect(ib).toContain(requirement.liveUse);
    }
    for (const faq of ibFaqEntries) expect(ib).toContain(faq.answer);

    const structures = lookupMarkdown("/defined-risk-options-structures") ?? "";
    for (const structure of catalog) {
      expect(structures).toContain(structure.name);
      expect(structures).toContain(formatLegs(structure));
      expect(structures).toContain(structure.guard_decision);
      expect(structures).toContain(structure.guard_reason);
    }
    for (const rule of guardLadder) {
      expect(structures).toContain(rule.rule);
      expect(structures).toContain(rule.note);
    }
    for (const faq of catalogFaqEntries) expect(structures).toContain(faq.answer);

    const kelly = lookupMarkdown("/fractional-kelly-position-sizing") ?? "";
    for (const row of workedExampleRows) {
      expect(kelly).toContain(row.step);
      expect(kelly).toContain(row.value);
    }
    for (const faq of kellySizingFaqEntries) expect(kelly).toContain(faq.answer);

    const uw = lookupMarkdown("/unusual-whales-interactive-brokers") ?? "";
    for (const surface of ingestSurfaces) expect(uw).toContain(surface.body);
    for (const stage of pipelineStages) expect(uw).toContain(stage.body);
    for (const requirement of uwRequirementRows) {
      expect(uw).toContain(requirement.supplies);
    }
    for (const faq of uwFaqEntries) expect(uw).toContain(faq.answer);

    const convex = lookupMarkdown("/convex-options-from-dark-pool-flow") ?? "";
    for (const gate of gateRows) expect(convex).toContain(gate.condition);
    for (const milestone of milestoneDetails) expect(convex).toContain(milestone.body);
    for (const play of playRows) expect(convex).toContain(play.convexityFloor);
    for (const faq of convexFaqEntries) expect(convex).toContain(faq.answer);

    const cri = lookupMarkdown("/crash-risk-index") ?? "";
    for (const component of criComponents) expect(cri).toContain(component.inputs);
    for (const band of criSignalBands) expect(cri).toContain(band.meaning);
    for (const faq of crashRiskIndexFaqEntries) expect(cri).toContain(faq.answer);
  });

  it("returns HTTP 404 markdown with sitemap and llms recovery links", () => {
    const result = markdownForSlug(["some-path-that-does-not-exist"]);
    expect(result.status).toBe(404);
    expect(result.body).toBe(notFoundMarkdown());
    expect(result.body).toContain("# Page not found");
    expect(result.body).toContain(`${siteUrl}/sitemap.xml`);
    expect(result.body).toContain(`${siteUrl}/llms.txt`);
    expect(result.body).toContain(`${siteUrl}/developers`);
    expect(NOT_FOUND_RECOVERY_LINKS.map((link) => link.href)).toEqual(
      expect.arrayContaining([
        `${siteUrl}/sitemap.xml`,
        `${siteUrl}/llms.txt`,
        `${siteUrl}/developers`,
      ]),
    );
  });

  it("publishes the acceptmarkdown media type and Vary contract", () => {
    expect(MARKDOWN_CONTENT_TYPE).toBe("text/markdown; charset=utf-8");
    expect(MARKDOWN_VARY).toBe("Accept, Accept-Encoding");
  });
});
