import { beforeEach, describe, expect, it, vi } from "vitest";
const { chat } = vi.hoisted(() => ({ chat: vi.fn() }));
vi.mock("@/lib/llm/provider", () => ({ chat }));
import { assertHumanizedCopy, auditHumanizerCopy, assertLocalHumanizer } from "@/lib/humanizer";
const clean = { title: "DXY tests 102.86", content: "Pictet says a close above 104.59 would target 110." };
const bad = "That's a conditional dollar-strength setup, not a confirmed breakout.";
beforeEach(() => { vi.clearAllMocks(); chat.mockResolvedValue({ text: '{"findings":[],"qualitative":[]}' }); });
describe("final Humanizer gate", () => {
  it.each([bad, "This isn't about returns. It's about resilience.", "Not only growth but also profits.", "That is a conditional setup. Not a confirmed breakout.", "That is a conditional setup. It is not a confirmed breakout."])("blocks negative parallelism even when a reviewer claims clean: %s", async content => {
    await expect(assertHumanizedCopy({ ...clean, content }, { provider: "xai", signal: new AbortController().signal })).rejects.toThrow(/Humanizer/);
  });
  it("preserves factual negation, source uncertainty, numeric ranges and named attribution", async () => {
    await expect(assertHumanizedCopy({ ...clean, content: "Pictet has not confirmed a breakout. A close above 104.59 could target 110. USD/JPY resistance is 158-159." }, { provider: "xai", signal: new AbortController().signal })).resolves.toMatchObject({ score: 100 });
    expect(chat).toHaveBeenCalledOnce();
    expect(chat.mock.calls[0][0].system).toContain("negative_parallelism");
  });
  it("computes every vocabulary occurrence and never trusts a model score", () => {
    const audit = auditHumanizerCopy({ title: "Robust robust robust", content: "Data." }, { findings: [], qualitative: [], score: 100 });
    expect(audit.score).toBe(85);
    expect(audit.findings).toHaveLength(3);
  });
  it("cannot merge several vocabulary penalties into a single model-supplied span", () => {
    const copy = { title: "Robust robust robust", content: "Data." };
    expect(() => auditHumanizerCopy(copy, { findings: [
      { field: "title", text: copy.title, category: "vocabulary" },
    ], qualitative: [] })).toThrow(/vocabulary/);
  });
  it("honors qualitative findings even above 90", () => {
    expect(() => auditHumanizerCopy(clean, { findings: [], qualitative: [{ field: "content", text: clean.content, category: "forced_threes" }] })).not.toThrow();
    chat.mockResolvedValue({ text: JSON.stringify({ findings: [], qualitative: [{ field: "content", text: clean.content, category: "forced_threes" }] }) });
    return expect(assertHumanizedCopy(clean, { provider: "xai", signal: new AbortController().signal })).rejects.toThrow(/Humanizer/);
  });
  it.each([{}, { findings: [], qualitative: null }, { findings: [{ field: "content", text: "invented", category: "vague_attribution" }], qualitative: [] }])("rejects malformed or fabricated findings", review => {
    expect(() => auditHumanizerCopy(clean, review)).toThrow(/Humanizer/);
  });
  it("does not blanket-ban contextual nouns or real data dates", () => {
    expect(() => assertLocalHumanizer({ title: "Daily highlights", content: "The landscape photograph is dated October 27, 2026. Revenue has not risen." })).not.toThrow();
  });
  it("carries unchanged findings even when the next reviewer drops them", () => {
    const previous = auditHumanizerCopy(clean, { findings: [], qualitative: [{ field: "content", text: "Pictet says", category: "filler" }] });
    const revised = auditHumanizerCopy(clean, { findings: [], qualitative: [] }, previous);
    expect(revised.qualitative).toEqual(previous.qualitative);
  });
  it("does not let a broad finding bridge two disjoint same-category passages", () => {
    const content = "This reflects broader conditions. It sets the stage for growth.";
    const first = "reflects broader conditions";
    const second = "sets the stage for growth";
    const audit = auditHumanizerCopy({ title: "Market note", content }, { findings: [
      { field: "content", text: first, category: "significance_inflation" },
      { field: "content", text: second, category: "significance_inflation" },
      { field: "content", text: content, category: "significance_inflation" },
    ], qualitative: [] });
    expect(audit.score).toBe(80);
    expect(audit.findings.map(finding => finding.text)).toEqual([first, second]);
  });
  it("keeps two carried findings when a new broad finding spans both", () => {
    const content = "This reflects broader conditions. It sets the stage for growth.";
    const first = "reflects broader conditions";
    const second = "sets the stage for growth";
    const copy = { title: "Market note", content };
    const previous = auditHumanizerCopy(copy, { findings: [
      { field: "content", text: first, category: "significance_inflation" },
      { field: "content", text: second, category: "significance_inflation" },
    ], qualitative: [] });
    const revised = auditHumanizerCopy(copy, { findings: [
      { field: "content", text: content, category: "significance_inflation" },
    ], qualitative: [] }, previous);
    expect(revised.score).toBe(80);
    expect(revised.findings.map(finding => finding.text)).toEqual([first, second]);
  });

});
