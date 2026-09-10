import { describe, it, expect } from "vitest";
import { parseResearchSource } from "../lib/newsfeedSource";
const path = "/api/newsfeed/research/files/" + "a".repeat(64);
const source = { kind: "dropbox", publisher: "Synthetic Bank", url: path + ".pdf", documentDate: "2026-09-07", folderDate: "2026-09-07", pages: [2], figures: [{ url: path + ".png", page: 2, caption: "Full axis chart" }], fileId: "id:fixture", revision: "r1", contentHash: "a".repeat(64) };
describe("private research provenance", () => {
  it("accepts source pages and private chart paths", () => expect(parseResearchSource(JSON.stringify(source))).toEqual(source));
  it("preserves text-only evidence without invented images", () => expect(parseResearchSource({...source, figures: []})?.figures).toEqual([]));
  it.each(["https://media.radon.run/chart.png", "javascript:alert(1)", "/api/newsfeed/research/files/../secret.png"])("rejects non-private image %s", url => expect(parseResearchSource({...source, figures: [{...source.figures[0], url}]})).toBeUndefined());
  it("rejects missing or malformed provenance", () => { expect(parseResearchSource("{")).toBeUndefined(); expect(parseResearchSource({...source, pages: [0]})).toBeUndefined(); });
});
