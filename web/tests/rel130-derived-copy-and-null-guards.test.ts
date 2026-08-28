/**
 * R-357 / R-376 / REL-130: three isolated correctness fixes.
 *
 * R-357: scan-window copy was hardcoded to "5 sessions" in three places while
 * the scan the server runs is `--days 20`, and the same file derived it
 * correctly in the footer. During a scan the operator was told the sample was
 * 5 sessions; when it landed the same screen said 20. Repo rule is derive,
 * never hardcode.
 *
 * R-376: the row-truncation loop introduced by REL-105(a) read
 * `data.tables[key].length` on the ORIGINAL (possibly null) value even though
 * the line above had defensively substituted `[]`, so a cache file carrying
 * `"tables": {"main": null}` threw TypeError and the share-image route 500'd.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = resolve(fileURLToPath(import.meta.url), "..");
const repo = resolve(here, "..", "..");

/** Comment lines stripped: a comment that quotes the old code satisfies a
 *  structural assertion for the wrong reason. */
function code(relPath: string): string {
  return readFileSync(resolve(repo, relPath), "utf8")
    .split("\n")
    .filter((l) => {
      const t = l.trimStart();
      return !t.startsWith("//") && !t.startsWith("*") && !t.startsWith("/*");
    })
    .join("\n");
}

describe("R-357 — the scan window is derived, never hardcoded", () => {
  const src = code("web/components/flow-analysis/TickerFlowReport.tsx");

  it("carries no hardcoded session count", () => {
    expect(src).not.toMatch(/5 sessions/);
    expect(src).not.toMatch(/last 5 trading sessions/);
  });

  it("declares one default and derives every window string from it", () => {
    expect(src).toContain("const DEFAULT_LOOKBACK_DAYS = 20");
    expect(src).toMatch(/\$\{lookbackDays\} sessions/);
    expect(src).toMatch(/\{lookbackDays\} trading sessions/);
    expect(src).toMatch(/lookback_days \?\? DEFAULT_LOOKBACK_DAYS/);
  });

  it("matches the --days the server actually passes", () => {
    const server = readFileSync(resolve(repo, "scripts", "api", "server.py"), "utf8");
    expect(server).toMatch(/--days["']?,\s*["']?20/);
  });
});

describe("R-376 — a null table value renders instead of throwing", () => {
  const src = code("web/app/api/menthorq/cta/image/route.tsx");

  it("never reads length off the original table value", () => {
    expect(src).not.toContain("budget -= data.tables[key].length");
  });

  it("assigns the slice unconditionally and decrements from it", () => {
    expect(src).toContain("data.tables[key] = kept");
    expect(src).toContain("budget -= kept.length");
  });
});

describe("R-376 — the truncation loop, executed", () => {
  /** The loop verbatim, so the behaviour is tested and not just the text. */
  function truncate(data: { tables: Record<string, unknown[] | null> }, max: number) {
    let budget = max;
    for (const key of Object.keys(data.tables)) {
      const rows = data.tables[key] ?? [];
      const kept = rows.slice(0, Math.max(0, budget));
      data.tables[key] = kept;
      budget -= kept.length;
    }
    return data;
  }

  it("survives a null table value", () => {
    const data = { tables: { main: null } };
    expect(() => truncate(data, 50)).not.toThrow();
    expect(data.tables.main).toEqual([]);
  });

  it("still truncates past the budget", () => {
    const data = { tables: { a: Array.from({ length: 40 }, (_, i) => i), b: Array.from({ length: 40 }, (_, i) => i) } };
    truncate(data, 50);
    expect(data.tables.a).toHaveLength(40);
    expect(data.tables.b).toHaveLength(10);
  });

  it("does not go negative on an exhausted budget", () => {
    const data = { tables: { a: [1, 2, 3], b: [1, 2, 3] } };
    truncate(data, 2);
    expect(data.tables.a).toHaveLength(2);
    expect(data.tables.b).toHaveLength(0);
  });
});
