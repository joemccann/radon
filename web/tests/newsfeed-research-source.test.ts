import { describe, it, expect } from "vitest";
import { parseResearchSource } from "../lib/newsfeedSource";
import { planCharts } from "../lib/planCharts";
const path = "/api/newsfeed/research/files/" + "a".repeat(64);
const source = { kind: "dropbox", publisher: "Synthetic Bank", url: path + ".pdf", documentDate: "2026-09-07", folderDate: "2026-09-07", pages: [2], figures: [{ url: path + ".png", page: 2, caption: "Full axis chart" }], fileId: "id:fixture", revision: "r1", contentHash: "a".repeat(64) };
describe("private research provenance", () => {
  it("accepts source pages and private chart paths", () => expect(parseResearchSource(JSON.stringify(source))).toEqual(source));
  it("preserves text-only evidence without invented images", () => expect(parseResearchSource({...source, figures: []})?.figures).toEqual([]));
  it.each(["https://media.radon.run/chart.png", "javascript:alert(1)", "/api/newsfeed/research/files/../secret.png"])("rejects non-private image %s", url => expect(parseResearchSource({...source, figures: [{...source.figures[0], url}]})).toBeUndefined());
  it("rejects missing or malformed provenance", () => { expect(parseResearchSource("{")).toBeUndefined(); expect(parseResearchSource({...source, pages: [0]})).toBeUndefined(); });
});
it("keeps a chart plan and drops a malformed one without dropping the source", () => {
  const charts = planCharts("Name A yields 3.5-4.0%. Name B sits at 2.1%.");
  expect(parseResearchSource({ ...source, figures: [], charts })?.charts).toEqual(charts);
  const parsed = parseResearchSource({ ...source, figures: [], charts: [{ kind: "levels" }] });
  expect(parsed?.figures).toEqual([]);
  expect(parsed?.charts).toBeUndefined();
});
it("retains a private agent-readable manifest URL and rejects external evidence", () => {
  expect(parseResearchSource({ ...source, evidenceUrl: path+".json" })?.evidenceUrl).toBe(path+".json");
  expect(parseResearchSource({ ...source, evidenceUrl: "https://attacker.example/evidence.json" })).toBeUndefined();
});

describe("marketdesk research provenance", () => {
  const sha = "c".repeat(64);
  const md = {
    kind: "marketdesk", publisher: "MarketDesk", articleId: "4AJs5Adl2Yp",
    articleUrl: "https://marketdesk.ai/library/browse/4AJs5Adl2Yp", contentHash: sha,
    documentDate: "2026-10-26", folderDate: "2026-10-26", pages: [1],
    url: `/api/newsfeed/research/files/${sha}.pdf`,
    figures: [{ url: path + ".png", caption: "Breadth thrust" }],
    summary: ["Breadth improved", "Credit spreads tightened"],
    extraImages: [`/api/newsfeed/research/files/${"d".repeat(64)}.png`],
  };
  it("accepts the SPEC 4.6 shape from an object or serialized JSON", () => {
    expect(parseResearchSource(md)).toEqual(md);
    expect(parseResearchSource(JSON.stringify(md))).toEqual(md);
  });
  it("narrows on kind and needs no figure page", () => {
    const parsed = parseResearchSource(md);
    expect(parsed?.kind).toBe("marketdesk");
    if (parsed?.kind === "marketdesk") {
      expect(parsed.articleId).toBe("4AJs5Adl2Yp");
      expect(parsed.figures[0]).toEqual({ url: path + ".png", caption: "Breadth thrust" });
    }
  });
  it("treats summary and extraImages as optional", () => {
    const { summary: _s, extraImages: _e, ...bare } = md;
    expect(parseResearchSource(bare)).toEqual(bare);
  });
  it("strips em dashes from publisher, captions and summary", () => {
    const parsed = parseResearchSource({ ...md, publisher: "Market — Desk", figures: [{ url: path + ".png", caption: "A — B" }], summary: ["Up — down"] });
    expect(parsed?.publisher).toBe("Market, Desk");
    expect(parsed?.figures[0].caption).toBe("A, B");
    expect(parsed?.kind === "marketdesk" && parsed.summary).toEqual(["Up, down"]);
  });
  it.each([
    ["non-private pdf", { url: "https://marketdesk.ai/standalone/x.pdf" }],
    ["png as pdf", { url: path + ".png" }],
    ["non-private figure", { figures: [{ url: "https://marketdesk.ai/a.png", caption: "x" }] }],
    ["pdf figure", { figures: [{ url: path + ".pdf", caption: "x" }] }],
    ["figure without caption", { figures: [{ url: path + ".png" }] }],
    ["non-private extra image", { extraImages: ["https://marketdesk.ai/a.png"] }],
    ["non-string summary", { summary: [1] }],
    ["summary not array", { summary: "bullet" }],
    ["bad article id", { articleId: "../x", articleUrl: "https://marketdesk.ai/library/browse/../x" }],
    ["article url mismatch", { articleUrl: "https://attacker.example/library/browse/4AJs5Adl2Yp" }],
    ["bad hash", { contentHash: "nothex" }],
    ["bad date", { documentDate: "Oct 26" }],
    ["empty publisher", { publisher: " " }],
    ["bad pages", { pages: [0] }],
    ["more than 12 figures", { figures: Array.from({ length: 13 }, () => ({ url: path + ".png", caption: "x" })) }],
    ["more than 20 summary bullets", { summary: Array.from({ length: 21 }, () => "b") }],
    ["summary bullet over 500 chars", { summary: ["x".repeat(501)] }],
    ["more than 40 extra images", { extraImages: Array.from({ length: 41 }, () => path + ".png") }],
  ])("rejects %s", (_label, patch) => expect(parseResearchSource({ ...md, ...patch })).toBeUndefined());
  it("still rejects unknown kinds", () => expect(parseResearchSource({ ...md, kind: "email" })).toBeUndefined());
  it("counts summary length in code points like the publisher, so an astral bullet still renders", () => {
    const bullet = "a".repeat(499) + "\u{1F4C8}";
    const parsed = parseResearchSource({ ...md, summary: [bullet] });
    expect(parsed?.kind === "marketdesk" && parsed.summary).toEqual([bullet]);
    expect(parseResearchSource({ ...md, summary: ["a".repeat(500) + "\u{1F4C8}"] })).toBeUndefined();
  });
});
