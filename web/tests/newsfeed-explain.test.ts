import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  EXPLAIN_THIN_MESSAGE,
  NEWSFEED_EXPLAIN_PROMPT_VERSION,
  NEWSFEED_EXPLAIN_SYSTEM,
  explainContentHash,
  explainInput,
  isThinExplainInput,
  stripExplainDisclaimer,
} from "@/lib/newsfeedExplain";

const fixture = JSON.parse(
  readFileSync(join(__dirname, "fixtures/newsfeed-explain-pjm.json"), "utf8"),
) as { title: string; content: string; explanation: string };

describe("newsfeed explain contract", () => {
  it("accepts a research post and hashes title, body, and image urls", () => {
    const input = explainInput({
      postId: "pjm-2028-capacity",
      title: fixture.title,
      content: fixture.content,
      imageUrls: ["https://media.radon.run/images/pjm.png"],
    });
    expect(input?.postId).toBe("pjm-2028-capacity");
    expect(input?.title).toContain("PJM");
    expect(explainContentHash(input!)).toMatch(/^[a-f0-9]{64}$/);
    expect(explainContentHash(input!)).not.toBe(
      explainContentHash({ ...input!, imageUrls: [] }),
    );
    const v1 = createHash("sha256")
      .update(JSON.stringify([input!.title, input!.content, input!.imageUrls]))
      .digest("hex");
    expect(NEWSFEED_EXPLAIN_PROMPT_VERSION).toBeGreaterThanOrEqual(3);
    expect(explainContentHash(input!)).not.toBe(v1);
  });

  it("rejects missing ids, oversized copy, and junk image lists", () => {
    expect(explainInput(null)).toBeNull();
    expect(explainInput({ title: "x", content: "y" })).toBeNull();
    expect(explainInput({ postId: "", title: "x", content: "enough body text here" })).toBeNull();
    expect(explainInput({ postId: "p", title: "", content: "enough body text here" })).toBeNull();
    expect(explainInput({
      postId: "p",
      title: "x",
      content: "y".repeat(12_001),
    })).toBeNull();
    expect(explainInput({
      postId: "p",
      title: "Capacity auction",
      content: "Cleared at the cap.",
      imageUrls: [1],
    })).toBeNull();
  });

  it("flags thin posts and keeps the canned sentence free of advice and em dashes", () => {
    const thin = explainInput({ postId: "t", title: "Hi", content: "Ok." });
    expect(isThinExplainInput(thin!)).toBe(true);
    expect(isThinExplainInput(explainInput({
      postId: "pjm-2028-capacity",
      title: fixture.title,
      content: fixture.content,
    })!)).toBe(false);
    expect(EXPLAIN_THIN_MESSAGE).toMatch(/enough detail/);
    expect(EXPLAIN_THIN_MESSAGE).not.toMatch(/—|&(?:mdash|#8212);/);
    expect(EXPLAIN_THIN_MESSAGE).not.toMatch(/buy|sell|invest/i);
  });

  it("pins a jargon-defining system prompt that forbids buy-sell copy and output disclaimers", () => {
    expect(NEWSFEED_EXPLAIN_SYSTEM).toMatch(/plain English/i);
    expect(NEWSFEED_EXPLAIN_SYSTEM).toMatch(/jargon/i);
    expect(NEWSFEED_EXPLAIN_SYSTEM).toMatch(/buy,\s*sell,\s*or hold/i);
    expect(NEWSFEED_EXPLAIN_SYSTEM).toMatch(/never (?:write|add) disclaimers/i);
    expect(NEWSFEED_EXPLAIN_SYSTEM).not.toMatch(/this is not investment advice/i);
    expect(NEWSFEED_EXPLAIN_SYSTEM).not.toMatch(/—|&(?:mdash|#8212);/);
    expect(NEWSFEED_EXPLAIN_SYSTEM).not.toMatch(/CEREBRAS/i);
    expect(NEWSFEED_EXPLAIN_SYSTEM).toMatch(/untrusted/i);
    expect(fixture.explanation).toMatch(/capacity auction/);
    expect(fixture.explanation).toMatch(/MW-day/);
    expect(fixture.explanation).toMatch(/collar/);
    expect(fixture.explanation).not.toMatch(/—/);
    expect(fixture.explanation).not.toMatch(/investment advice|financial advice|not a recommendation/i);
  });

  it("never labels the reader as ordinary or a non-expert", () => {
    expect(NEWSFEED_EXPLAIN_SYSTEM).not.toMatch(/ordinary|non-expert|layperson|everyday people|average person/i);
    expect(NEWSFEED_EXPLAIN_SYSTEM).toMatch(/never describe or label the reader/i);
    expect(fixture.explanation).not.toMatch(/ordinary people|non-expert|layperson|everyday people/i);
  });

  it("strips disclaimer boilerplate the model may still append", () => {
    const body = "PJM paid plants to stay available. A capacity auction buys standby power.";
    expect(stripExplainDisclaimer(`${body} This is not investment advice.`)).toBe(body);
    expect(stripExplainDisclaimer(`${body} This is not financial advice.`)).toBe(body);
    expect(stripExplainDisclaimer(`${body} Not a recommendation to buy or sell.`)).toBe(body);
    expect(stripExplainDisclaimer(body)).toBe(body);
    expect(stripExplainDisclaimer(`${body} This can raise capacity charges on power bills.`))
      .toBe(`${body} This can raise capacity charges on power bills.`);
  });
});
