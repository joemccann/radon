import { describe, expect, it } from "vitest";
import { clusterPages } from "./cluster-pages";
import {
  dossierMarkdown,
  escapeMarkdownTableCell,
  markdownTable,
} from "./dossier-markdown";

describe("dossier Markdown formatters", () => {
  it("escapes pipes, backslashes, and line breaks inside table cells", () => {
    expect(escapeMarkdownTableCell("left | right\\tail\nnext")).toBe(
      "left \\| right\\\\tail<br>next",
    );
    expect(markdownTable(["Name"], [["A | B"]])).toContain("A \\| B");
  });

  it("has a substantive Markdown mirror for every registered dossier", () => {
    for (const page of clusterPages) {
      const body = dossierMarkdown(page.slug);
      expect(body).not.toBeNull();
      expect(body?.match(/^## /gm)?.length ?? 0).toBeGreaterThanOrEqual(3);
      expect(body).toContain(page.lastModified);
    }
  });
});
