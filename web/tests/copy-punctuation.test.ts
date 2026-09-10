import { describe, expect, it } from "vitest";
import { withoutEmDashes } from "../lib/copyPunctuation";

describe("authored copy punctuation", () => {
  it.each(["—", "&mdash;", "&#8212;", "&#x2014;", "&#X2014;"])("removes %s without losing the clause", dash => {
    expect(withoutEmDashes(`Positioning ${dash} the tell ${dash} remains neutral.`)).toBe("Positioning, the tell, remains neutral.");
  });

  it("keeps numeric ranges readable without changing signed financial values", () => {
    expect(withoutEmDashes("10—20%; -2.5%; −3%; 5–7%; risk-adjusted. Evidence — mixed."))
      .toBe("10 to 20%; -2.5%; −3%; 5–7%; risk-adjusted. Evidence, mixed.");
  });

  it("preserves Markdown indentation, hard breaks, links, and decimal values", () => {
    const input = "## Flows\n\n- **Buyers** — $14.5bn.  \n  - *Retail* added $7.8bn.\n\n[Source](https://example.com/research?id=2026-09-08)";
    expect(withoutEmDashes(input)).toBe(input.replace(" — ", ", "));
  });

  it("preserves URL destinations and produces stable output on repeated normalization", () => {
    const input = "[Evidence — August](https://example.com/research/yen—flows?date=2026-09-08)\n\nReturns: -2.5%.";
    const expected = "[Evidence, August](https://example.com/research/yen%E2%80%94flows?date=2026-09-08)\n\nReturns: -2.5%.";
    expect(withoutEmDashes(input)).toBe(expected);
    expect(withoutEmDashes(expected)).toBe(expected);
    expect(new URL("https://example.com/research/yen—flows?date=2026-09-08").href)
      .toBe("https://example.com/research/yen%E2%80%94flows?date=2026-09-08");
  });

  it("keeps em-dash list items as Markdown bullets", () => {
    expect(withoutEmDashes("— First observation\n  — Supporting evidence"))
      .toBe("- First observation\n  - Supporting evidence");
  });

  it("preserves nested URL parentheses without treating following prose as the destination", () => {
    expect(withoutEmDashes("[Evidence — August](https://example.com/research/(fx(spot))—flows)—Read more."))
      .toBe("[Evidence, August](https://example.com/research/(fx(spot))%E2%80%94flows), Read more.");
  });
});
