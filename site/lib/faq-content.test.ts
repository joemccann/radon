import { describe, expect, it } from "vitest";
import { faqEntries, faqStructuredData } from "./faq-content";

const AI_CLICHES = /seamless|unleash|elevate|game-changer|next-gen/i;
const EM_OR_EN_DASH = /[–—]/;

describe("FAQ content contract", () => {
  it("publishes six to eight questions", () => {
    expect(faqEntries.length).toBeGreaterThanOrEqual(6);
    expect(faqEntries.length).toBeLessThanOrEqual(8);
  });

  it("keeps every entry a real question with a substantive answer", () => {
    for (const entry of faqEntries) {
      expect(entry.question.trim().endsWith("?")).toBe(true);
      expect(entry.answer.trim().length).toBeGreaterThan(80);
    }
  });

  it("bans em dashes and AI cliches from the copy", () => {
    for (const entry of faqEntries) {
      expect(entry.question).not.toMatch(EM_OR_EN_DASH);
      expect(entry.answer).not.toMatch(EM_OR_EN_DASH);
      expect(entry.answer).not.toMatch(AI_CLICHES);
    }
  });

  it("is honest about broker requirements", () => {
    const corpus = faqEntries.map((entry) => entry.answer).join(" ");
    expect(corpus).toContain("Interactive Brokers");
    expect(corpus).toContain("demo.radon.run");
    expect(corpus).not.toMatch(/Robinhood is (integrated|supported)/i);
  });

  it("generates FAQPage schema that mirrors the visible content exactly", () => {
    const schema = faqStructuredData();
    expect(schema["@context"]).toBe("https://schema.org");
    expect(schema["@type"]).toBe("FAQPage");
    expect(schema.mainEntity).toHaveLength(faqEntries.length);
    schema.mainEntity.forEach((question, index) => {
      expect(question["@type"]).toBe("Question");
      expect(question.name).toBe(faqEntries[index].question);
      expect(question.acceptedAnswer.text).toBe(faqEntries[index].answer);
    });
  });
});
