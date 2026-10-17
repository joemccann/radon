/**
 * Shipped UI copy carries no generated-UI tells.
 *
 * Ban list adopted from the redesign audit in Leonxlnx/taste-skill (MIT) and
 * kept to copy only: Radon's own design system (Inter, IBM Plex Mono figures,
 * hairline tables) overrides that skill's aesthetic bans. Full rationale and
 * the visual tells live in `.claude/skills/no-slop-app/SKILL.md`.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const BANNED: Array<[string, RegExp]> = [
  ["AI cliche", /\b(?:elevate (?:your|the)|seamless(?:ly)?|unleash(?:es|ed)?|next-gen|game[- ]changer|delve|tapestry|in the world of)\b/i],
  ["apology filler", /\bOops\b/],
  ["placeholder text", /\blorem ipsum\b/i],
  ["placeholder person", /\b(?:John|Jane) (?:Doe|Smith)\b/],
  ["placeholder company", /\bAcme(?: Corp)?\b/],
];

const ROOTS = ["../app", "../components", "../../site/app", "../../site/components"].map(p => join(__dirname, p));

function walk(dir: string): string[] {
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return [];
  }
  return entries.flatMap(name => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return name === "node_modules" ? [] : walk(p);
    return /\.tsx?$/.test(p) && !/\.test\.tsx?$/.test(p) ? [p] : [];
  });
}

/** Lines of source with comments removed, so a code comment never trips the copy rule. */
export function copyViolations(source: string): Array<{ line: number; rule: string; text: string }> {
  const stripped = source.replace(/\/\*[\s\S]*?\*\//g, m => m.replace(/[^\n]/g, " "));
  const out: Array<{ line: number; rule: string; text: string }> = [];
  stripped.split("\n").forEach((raw, i) => {
    const text = raw.replace(/(^|[^:"'`])\/\/.*$/, "$1");
    for (const [rule, re] of BANNED) if (re.test(text)) out.push({ line: i + 1, rule, text: text.trim() });
  });
  return out;
}

describe("UI copy slop contract", () => {
  it("flags banned copy and ignores comments", () => {
    const sample = [
      "// Oops is fine in a comment",
      "/* Elevate your trading */",
      'const title = "Elevate your edge";',
      "<p>Oops, something broke</p>",
      'const user = "John Doe";',
    ].join("\n");
    expect(copyViolations(sample).map(v => [v.line, v.rule])).toEqual([
      [3, "AI cliche"],
      [4, "apology filler"],
      [5, "placeholder person"],
    ]);
  });

  it("no shipped web or site source carries generated-UI copy", () => {
    const files = ROOTS.flatMap(walk);
    expect(files.length).toBeGreaterThan(100);
    const hits = files.flatMap(f =>
      copyViolations(readFileSync(f, "utf8")).map(v => `${relative(join(__dirname, "../.."), f)}:${v.line} ${v.rule}: ${v.text}`),
    );
    expect(hits).toEqual([]);
  });
});
