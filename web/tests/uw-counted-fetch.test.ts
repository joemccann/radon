/**
 * REL-036 / R-062 — every Next.js UW fetch must move the shared daily budget.
 *
 * UWClient (Python) records each UW HTTP hit into the flock-shared budget
 * file; the six route-handler call sites fetched UW directly and incremented
 * nothing, so browsing-driven traffic was invisible to /uw/usage and the
 * universe-scan brake. countedUwFetch mirrors one hit per UW response into
 * the shared counter via POST /uw/usage/record (fire-and-forget).
 */
import { readdirSync, readFileSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { afterEach, describe, expect, it, vi } from "vitest";

const { radonFetchMock } = vi.hoisted(() => ({
  radonFetchMock: vi.fn(async () => ({})),
}));

vi.mock("@/lib/radonApi", () => ({ radonFetch: radonFetchMock }));

import { countedUwFetch } from "@/lib/uwCountedFetch";

const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

afterEach(() => {
  vi.unstubAllGlobals();
  radonFetchMock.mockClear();
});

describe("countedUwFetch", () => {
  it("records one budget hit per resolved UW fetch", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));

    await countedUwFetch("https://api.unusualwhales.com/api/stock/AAPL/info");
    await countedUwFetch("https://api.unusualwhales.com/api/stock/AAPL/stock-state");
    await countedUwFetch("https://api.unusualwhales.com/api/news/headlines");

    expect(radonFetchMock).toHaveBeenCalledTimes(3);
    expect(radonFetchMock).toHaveBeenCalledWith(
      "/uw/usage/record?caller=web&endpoint=stock%2FAAPL%2Finfo",
      expect.objectContaining({ method: "POST" }),
    );
    // The spent endpoint travels with the hit — a bare total cannot say
    // which UW path browsing traffic burned.
    expect(radonFetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/uw/usage/record?caller=web&endpoint=stock%2FAAPL%2Finfo",
      "/uw/usage/record?caller=web&endpoint=stock%2FAAPL%2Fstock-state",
      "/uw/usage/record?caller=web&endpoint=news%2Fheadlines",
    ]);
  });

  it("records nothing when the UW request never produced a response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );

    await expect(
      countedUwFetch("https://api.unusualwhales.com/api/stock/AAPL/info"),
    ).rejects.toThrow("network down");
    expect(radonFetchMock).not.toHaveBeenCalled();
  });

  it("never lets a failed budget record break the UW data path", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    radonFetchMock.mockRejectedValueOnce(new Error("fastapi down"));

    const res = await countedUwFetch("https://api.unusualwhales.com/api/stock/AAPL/info");

    expect(res.status).toBe(200);
  });
});

/**
 * T-120 — the adoption guard is DERIVED from the tree, not a literal list.
 *
 * The previous form hardcoded four route paths and compared two regex counts
 * over raw text. It was complete on the day it was written, but a fifth route
 * added tomorrow is simply absent from the list: it spends the quota
 * invisibly and the suite stays green (R-062 verbatim). Counting raw text also
 * accepted a `countedUwFetch(` inside a comment.
 *
 * These helpers are exercised directly against synthetic sources below, then
 * run over every real file under `web/app` that touches UW.
 */
const UW_HOST = "api.unusualwhales.com";

/** Remove line and block comments so no rule can be satisfied by prose.
 *
 * `//` after a colon is a URL scheme, not a comment — stripping those was
 * what silently emptied the first draft of this sweep.
 */
export function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

/** Files under `dir` whose code (not comments) reaches Unusual Whales. */
export function collectUwFiles(dir: string): string[] {
  const found: string[] = [];
  const walk = (current: string) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (!/\.(ts|tsx)$/.test(entry.name)) continue;
      const code = stripComments(readFileSync(full, "utf-8"));
      if (code.includes("unusualwhales") || code.includes("UW_TOKEN")) {
        found.push(path.relative(WEB_DIR, full));
      }
    }
  };
  walk(dir);
  return found.sort();
}

/** Every way a file can reach UW without moving the shared daily budget.
 *
 * Two shapes, both of which the previous count-the-regexes form accepted:
 *   1. a bare `fetch(` whose arguments name the UW host directly;
 *   2. a UW base URL parked in a variable that a bare `fetch(` then uses.
 * `countedUwFetch(` matches neither scan — the lookbehind rejects a preceding
 * word character.
 *
 * Known limitation, stated rather than hidden: an identifier assigned from a
 * UW literal in one place and from a non-UW literal in another is dropped from
 * the derived set, so a file that shadows a UW base with an unrelated URL under
 * the same name would slip shape 2. Shape 1 still catches it at the call.
 */
const BARE_FETCH = /(?<![\w$.])fetch\s*\(/g;
const UW_HOST_RE = /api\.unusualwhales\.com/;

function uwDerivedIdentifiers(code: string): Set<string> {
  const uw = new Set<string>();
  const other = new Set<string>();
  const declaration = /(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:new\s+URL\()?\s*([\`"'])([^\`"']*)\2/g;
  for (let m = declaration.exec(code); m; m = declaration.exec(code)) {
    (UW_HOST_RE.test(m[3]) ? uw : other).add(m[1]);
  }
  for (const name of other) uw.delete(name);
  return uw;
}

export function uwCountingViolations(relative: string, raw: string): string[] {
  const problems: string[] = [];
  const code = stripComments(raw);

  if (!/from\s+["']@\/lib\/uwCountedFetch["']/.test(code)) {
    problems.push(`${relative}: reaches UW but never imports @/lib/uwCountedFetch`);
  }

  const derived = uwDerivedIdentifiers(code);
  BARE_FETCH.lastIndex = 0;
  for (let call = BARE_FETCH.exec(code); call; call = BARE_FETCH.exec(code)) {
    const args = code.slice(call.index, call.index + 300);
    const line = code.slice(0, call.index).split("\n").length;
    if (UW_HOST_RE.test(args)) {
      problems.push(`${relative}:${line}: bare fetch() against ${UW_HOST} — invisible to /uw/usage`);
      continue;
    }
    const used = [...derived].find((name) => new RegExp(`(?<![\\w$.])${name}(?![\\w$])`).test(args));
    if (used) {
      problems.push(
        `${relative}:${line}: bare fetch() on "${used}", which holds a ${UW_HOST} URL — invisible to /uw/usage`,
      );
    }
  }

  return problems;
}

describe("the UW adoption guard itself", () => {
  // If these ever pass on the bad inputs, the sweep below is decorative.
  const BARE = `import { NextResponse } from "next/server";
export async function GET() {
  const res = await fetch(\`https://${UW_HOST}/api/stock/AAPL/flow-alerts\`);
  return NextResponse.json(await res.json());
}`;
  const COUNTED = `import { countedUwFetch } from "@/lib/uwCountedFetch";
export async function GET() {
  const res = await countedUwFetch(\`https://${UW_HOST}/api/stock/AAPL/flow-alerts\`);
  return Response.json(await res.json());
}`;

  it("flags a new route that fetches UW directly", () => {
    const problems = uwCountingViolations("app/api/ticker/flow/route.ts", BARE);
    expect(problems).toHaveLength(2);
    expect(problems[0]).toContain("never imports @/lib/uwCountedFetch");
    expect(problems[1]).toContain("bare fetch() against api.unusualwhales.com");
  });

  it("accepts the same route once it goes through the wrapper", () => {
    expect(uwCountingViolations("app/api/ticker/flow/route.ts", COUNTED)).toEqual([]);
  });

  it("cannot be satisfied by a countedUwFetch mentioned in a comment", () => {
    const commented = BARE.replace(
      'import { NextResponse } from "next/server";',
      '// this route uses countedUwFetch from "@/lib/uwCountedFetch"\nimport { NextResponse } from "next/server";',
    );
    expect(uwCountingViolations("app/api/ticker/flow/route.ts", commented)).not.toEqual([]);
  });

  it("flags a UW URL parked in a module constant and fetched bare later", () => {
    const viaConstant = `import { NextResponse } from "next/server";
const UW = "https://${UW_HOST}/api";
export async function GET() {
  const res = await fetch(\`\${UW}/stock/AAPL/info\`);
  return NextResponse.json(await res.json());
}`;
    expect(uwCountingViolations("app/api/x/route.ts", viaConstant)).not.toEqual([]);
  });

  it("accepts a UW base constant that is handed to the wrapper", () => {
    // `app/api/ticker/news/route.ts` does exactly this with `new URL(...)`.
    const viaWrapper = `import { countedUwFetch } from "@/lib/uwCountedFetch";
export async function GET() {
  const url = new URL("https://${UW_HOST}/api/news/headlines");
  url.searchParams.set("limit", "10");
  const res = await countedUwFetch(url.toString());
  return Response.json(await res.json());
}`;
    expect(uwCountingViolations("app/api/x/route.ts", viaWrapper)).toEqual([]);
  });

  it("does not flag the unrelated providers a UW route also calls", () => {
    // `app/api/ticker/info/route.ts` fetches Exa and Yahoo bare, by design.
    const mixed = `import { countedUwFetch } from "@/lib/uwCountedFetch";
export async function GET() {
  await countedUwFetch("https://${UW_HOST}/api/stock/AAPL/info");
  const url = "https://query1.finance.yahoo.com/v8/finance/chart/AAPL";
  await fetch(url, { cache: "no-store" });
  await fetch("https://api.exa.ai/search", { method: "POST" });
  return Response.json({});
}`;
    expect(uwCountingViolations("app/api/x/route.ts", mixed)).toEqual([]);
  });
});

describe("every UW call site under web/app goes through the counted path", () => {
  const uwFiles = collectUwFiles(path.join(WEB_DIR, "app"));

  it("finds the UW-touching routes by walking the tree, not by a literal list", () => {
    // Non-empty is the point: an empty walk would make the sweep vacuous.
    expect(uwFiles.length).toBeGreaterThan(0);
  });

  it.each(uwFiles)("%s counts every UW fetch", (relative) => {
    const raw = readFileSync(path.join(WEB_DIR, relative), "utf-8");
    expect(uwCountingViolations(relative, raw)).toEqual([]);
  });
});
