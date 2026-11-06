/**
 * Next.js routes read snapshot state from Turso and IB, never from `data/`.
 * When Turso fails, a route serves the last in-memory read (`lib/dbFirstRead`).
 *
 * A file under `web/app/api` that names a path inside `data/`, in code or in a
 * comment, fails here unless it is on the allowlist with a reason a reviewer can
 * check. New snapshot routes do not belong on it: read Turso instead.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const WEB_ROOT = join(__dirname, "..");
const API_ROOT = join(WEB_ROOT, "app", "api");

/** Route file (relative to `web/app/api`) -> why it may still name `data/`. */
const ALLOWLIST: Record<string, string> = {
  "flex-token/route.ts":
    "data/flex_token_config.json is operator configuration with no Turso table, not a snapshot.",
  "flow-analysis/[ticker]/route.ts":
    "data/flow_reports/<TICKER>.json and .scan.json are FastAPI's per-ticker report cache and scan status; no Turso table holds the report.",
  "futures/chain/route.ts":
    "data/futures_chain_<SYMBOL>.json is FastAPI's IB contract-list cache; no Turso table holds the chain.",
  "informed-flow/[ticker]/route.ts":
    "data/informed_flow/<TICKER>.json is FastAPI's per-ticker cache; no Turso table holds the surface.",
  "menthorq/[command]/image/route.tsx":
    "data/menthorq_cache/<command>_*.json: only the cta command has a Turso table (menthorq_cta).",
  "ticker/info/route.ts":
    "data/company_info_cache is this route's own read-through vendor cache, not snapshot state.",
  "ticker/seasonality/route.ts":
    "data/seasonality_cache is this route's own read-through vendor cache, not snapshot state.",
  "regime/route.ts":
    "Write-only: data/cri_scheduled archive is read by scripts/strength_confirmation_scanner.py and scripts/generate_regime_share.py. The route reads only Turso.",
  "internals/route.ts":
    "Write-only: data/cri_scheduled archive is read by scripts/strength_confirmation_scanner.py and scripts/generate_regime_share.py. The route reads only Turso.",
};

const JSON_PATH = /\bdata\/[\w./<>{}$-]*\.json\b/;
const JOINED_SEGMENT = /["'`]data["'`]\s*,\s*["'`]/;
const PATH_SEGMENT = /["'`]data["'`]\s*\)/;

function listRouteFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return listRouteFiles(path);
    return /\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

function namesDataPath(source: string): boolean {
  return JSON_PATH.test(source) || JOINED_SEGMENT.test(source) || PATH_SEGMENT.test(source);
}

describe("API routes do not read snapshot state from data/", () => {
  const files = listRouteFiles(API_ROOT).map((path) => ({
    route: relative(API_ROOT, path).split("\\").join("/"),
    source: readFileSync(path, "utf-8"),
  }));

  it("scans the real route tree", () => {
    expect(files.length).toBeGreaterThan(100);
  });

  it("no route outside the allowlist names a path inside data/", () => {
    const offenders = files
      .filter(({ route, source }) => !(route in ALLOWLIST) && namesDataPath(source))
      .map(({ route }) => route);
    expect(offenders).toEqual([]);
  });

  it("every allowlist entry still names data/, so the list cannot rot", () => {
    const stale = Object.keys(ALLOWLIST).filter((route) => {
      const file = files.find((candidate) => candidate.route === route);
      return !file || !namesDataPath(file.source);
    });
    expect(stale).toEqual([]);
  });

  it("no route using dbFirstRead imports the filesystem", () => {
    const offenders = files
      .filter(({ source }) => /\bdbFirstRead\(/.test(source))
      .filter(({ route, source }) =>
        !(route in ALLOWLIST) && /from\s+["'](node:)?fs(\/promises)?["']/.test(source),
      )
      .map(({ route }) => route);
    expect(offenders).toEqual([]);
  });

  it("the write-only entries never read from data/", () => {
    for (const route of ["regime/route.ts", "internals/route.ts"]) {
      const file = files.find((candidate) => candidate.route === route);
      expect(file, route).toBeDefined();
      expect(file!.source, route).not.toMatch(/\b(readFile|readdir|stat|existsSync)\b/);
    }
  });
});
