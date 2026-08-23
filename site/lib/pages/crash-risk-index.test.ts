import { describe, expect, it } from "vitest";
import {
  CRASH_RISK_INDEX_DATE_MODIFIED,
  CRASH_RISK_INDEX_DATE_PUBLISHED,
  CRASH_RISK_INDEX_DESCRIPTION,
  CRASH_RISK_INDEX_H1,
  CRASH_RISK_INDEX_TITLE,
  crashRiskIndexFaqEntries,
  crashRiskIndexStructuredData,
  criComponents,
  criSignalBands,
} from "./crash-risk-index";

const EM_OR_EN_DASH = /[–—]/;
// \belevate\b, not bare "elevate": "Elevated" is the published 25 to 49 band
// name from the method note, not marketing copy.
const AI_CLICHES = /seamless|unleash|\belevate\b|game-changer|next-gen/i;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

// The composite that shipped in cri_scan.py / criCalc.ts has four published
// components and no options-surface inputs. These claims were wrong and must
// never come back.
const DEBUNKED_CLAIMS =
  /skew|term.structure|realized.implied|implied.realized|drawdown onset|proprietary/i;

function copyCorpus(): string {
  return [
    CRASH_RISK_INDEX_TITLE,
    CRASH_RISK_INDEX_DESCRIPTION,
    CRASH_RISK_INDEX_H1,
    ...crashRiskIndexFaqEntries.flatMap((entry) => [
      entry.question,
      entry.answer,
    ]),
    ...criComponents.flatMap((row) => [
      row.name,
      row.weight,
      row.inputs,
      row.read,
    ]),
    ...criSignalBands.flatMap((band) => [band.range, band.level, band.meaning]),
  ].join(" ");
}

describe("crash-risk-index content contract", () => {
  it("keeps the SEO surface within limits", () => {
    expect(CRASH_RISK_INDEX_TITLE.length).toBeLessThanOrEqual(60);
    expect(CRASH_RISK_INDEX_TITLE.endsWith(" | Radon Terminal")).toBe(true);
    expect(CRASH_RISK_INDEX_TITLE).toContain("CRI Crash");
    expect(CRASH_RISK_INDEX_DESCRIPTION.length).toBeLessThanOrEqual(200);
    expect(CRASH_RISK_INDEX_DESCRIPTION).toContain("Crash Risk Index");
  });

  it("describes the real composite: four components scored 0 to 25", () => {
    expect(criComponents.map((row) => row.name)).toEqual([
      "VIX",
      "VVIX",
      "Correlation",
      "Momentum",
    ]);
    for (const row of criComponents) {
      expect(row.weight).toBe("0 to 25");
    }
    const corpus = copyCorpus();
    for (const input of ["VIX", "VVIX", "COR1M", "100-day"]) {
      expect(corpus).toContain(input);
    }
  });

  it("publishes the four signal bands from the method note", () => {
    expect(criSignalBands.map((band) => band.level)).toEqual([
      "Low",
      "Elevated",
      "High",
      "Critical",
    ]);
    expect(criSignalBands.map((band) => band.range)).toEqual([
      "0 to 24",
      "25 to 49",
      "50 to 74",
      "75 to 100",
    ]);
  });

  it("never repeats the debunked surface-input story", () => {
    expect(copyCorpus()).not.toMatch(DEBUNKED_CLAIMS);
  });

  it("bans em and en dashes and AI cliches from the copy", () => {
    const corpus = copyCorpus();
    expect(corpus).not.toMatch(EM_OR_EN_DASH);
    expect(corpus).not.toMatch(AI_CLICHES);
  });

  it("keeps every FAQ entry a real question with a substantive answer", () => {
    expect(crashRiskIndexFaqEntries).toHaveLength(4);
    for (const entry of crashRiskIndexFaqEntries) {
      expect(entry.question.trim().endsWith("?")).toBe(true);
      expect(entry.answer.trim().length).toBeGreaterThan(80);
    }
  });

  it("emits breadcrumb, article, and FAQ schema mirroring the copy", () => {
    expect(CRASH_RISK_INDEX_DATE_PUBLISHED).toMatch(ISO_DATE);
    expect(CRASH_RISK_INDEX_DATE_MODIFIED).toMatch(ISO_DATE);
    const [breadcrumb, article, faq] = crashRiskIndexStructuredData();
    expect(breadcrumb["@type"]).toBe("BreadcrumbList");
    expect(article).toMatchObject({
      "@type": "TechArticle",
      headline: CRASH_RISK_INDEX_H1,
      description: CRASH_RISK_INDEX_DESCRIPTION,
    });
    expect(faq["@type"]).toBe("FAQPage");
    const faqSchema = faq as {
      mainEntity: Array<{
        name: string;
        acceptedAnswer: { text: string };
      }>;
    };
    expect(faqSchema.mainEntity).toHaveLength(crashRiskIndexFaqEntries.length);
    faqSchema.mainEntity.forEach((question, index) => {
      expect(question.name).toBe(crashRiskIndexFaqEntries[index].question);
      expect(question.acceptedAnswer.text).toBe(
        crashRiskIndexFaqEntries[index].answer,
      );
    });
  });
});
