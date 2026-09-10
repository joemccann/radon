import { describe, expect, it } from "vitest";
import { editorialNavLinks } from "./editorial-content";
import { faqEntries } from "./faq-content";
import { llmsTxt } from "./llms-txt";
import { SITE_DESCRIPTION, SITE_TITLE } from "./seo";

const EM_OR_EN_DASH = /[–—]/;

describe("two-system value proposition", () => {
  it("names view construction and expression in the site description", () => {
    expect(SITE_DESCRIPTION.length).toBeLessThanOrEqual(160);
    expect(SITE_DESCRIPTION.toLowerCase()).toMatch(/view|construct/);
    expect(SITE_DESCRIPTION.toLowerCase()).toMatch(/express/);
    expect(SITE_DESCRIPTION).toContain("dark-pool");
    expect(SITE_DESCRIPTION).toContain("Unusual Whales");
    expect(SITE_DESCRIPTION).toContain("GEX");
    expect(SITE_DESCRIPTION).toContain("Interactive Brokers");
    expect(SITE_DESCRIPTION.toLowerCase()).toContain("options");
    expect(SITE_DESCRIPTION).not.toMatch(EM_OR_EN_DASH);
  });

  it("keeps the title on searchable entities", () => {
    expect(SITE_TITLE.startsWith("Radon Terminal | ")).toBe(true);
    expect(SITE_TITLE.length).toBeLessThanOrEqual(60);
    expect(SITE_TITLE).toContain("Dark Pool Flow");
    expect(SITE_TITLE).toContain("GEX");
    expect(SITE_TITLE).toContain("Options");
  });

  it("answers What is Radon with both systems, not gates alone", () => {
    const radon = faqEntries.find((entry) => entry.question === "What is Radon?");
    expect(radon).toBeDefined();
    expect(radon!.answer.toLowerCase()).toMatch(/point of view|construct/);
    expect(radon!.answer.toLowerCase()).toMatch(/express/);
    expect(radon!.answer).toMatch(/gate/i);
    expect(radon!.answer).not.toMatch(EM_OR_EN_DASH);
  });

  it("exposes View and Expression in primary nav", () => {
    const labels = editorialNavLinks.map((link) => link.label);
    expect(labels).toContain("View");
    expect(labels).toContain("Expression");
    expect(labels).toContain("Discipline");
  });

  it("describes both systems to agents", () => {
    expect(llmsTxt.toLowerCase()).toMatch(/point of view|construct a .*view|view construction/);
    expect(llmsTxt.toLowerCase()).toMatch(/express/);
  });
});
