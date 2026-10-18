import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { classifyRateTier } from "@/lib/demo/rateTier";
import {
  DEMO_ROUTE_POLICY,
  type DemoHttpMethod,
  type DemoMethodPolicy,
} from "@/lib/demo/routePolicy";

// Every API route must declare what a demo principal gets (lib/demo/routePolicy.ts).
// New features kept shipping working on app.radon.run and broken on
// demo.radon.run: no demo branch, an operator-only guard on a demo surface,
// or a Turso table nothing ever fills in the demo database.

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));
const REPO_ROOT = resolve(WEB_ROOT, "..");
const METHODS: DemoHttpMethod[] = ["GET", "POST", "PUT", "PATCH", "DELETE"];

// ── route tree ──────────────────────────────────────────────────────────────

type RouteFile = { route: string; file: string; source: string; methods: DemoHttpMethod[] };

function methodPattern(method: string): RegExp {
  return new RegExp(`export\\s+(?:async\\s+)?(?:function|const)\\s+${method}\\b`);
}

function collectRoutes(): RouteFile[] {
  const apiRoot = resolve(WEB_ROOT, "app", "api");
  const found: RouteFile[] = [];
  function walk(dir: string): void {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (/^route\.tsx?$/.test(entry.name)) {
        const source = readFileSync(full, "utf8");
        found.push({
          route: `/api/${relative(apiRoot, dir).split("\\").join("/")}`,
          file: full,
          source,
          methods: METHODS.filter((m) => methodPattern(m).test(source)),
        });
      }
    }
  }
  walk(apiRoot);
  return found.sort((a, b) => a.route.localeCompare(b.route));
}

/** The exported handler's own text, up to the next top-level export. */
function methodBody(source: string, method: string): string {
  const start = source.search(methodPattern(method));
  if (start === -1) return "";
  const rest = source.slice(start + 1);
  const next = rest.search(/\nexport\s/);
  return next === -1 ? rest : rest.slice(0, next);
}

// ── import graph (route file + every in-repo module it reaches) ────────────

function resolveImport(from: string, spec: string): string | null {
  let base: string;
  if (spec.startsWith("@/")) base = join(WEB_ROOT, spec.slice(2));
  else if (spec.startsWith("@tools/")) base = join(REPO_ROOT, "lib", "tools", spec.slice(7));
  else if (spec.startsWith(".")) base = resolve(dirname(from), spec);
  else return null;
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, join(base, "index.ts")]) {
    if (/\.tsx?$/.test(candidate) && existsSync(candidate)) return candidate;
  }
  return null;
}

const closureCache = new Map<string, string>();

function importClosureText(entry: string): string {
  const cached = closureCache.get(entry);
  if (cached !== undefined) return cached;
  const seen = new Set<string>();
  const stack = [entry];
  const parts: string[] = [];
  while (stack.length > 0) {
    const file = stack.pop()!;
    if (seen.has(file)) continue;
    seen.add(file);
    const text = readFileSync(file, "utf8");
    parts.push(text);
    for (const m of text.matchAll(/(?:from|import)\s*\(?\s*["']([^"']+)["']/g)) {
      const next = resolveImport(file, m[1]);
      if (next) stack.push(next);
    }
  }
  const joined = parts.join("\n");
  closureCache.set(entry, joined);
  return joined;
}

// ── what produces rows in the demo Turso ────────────────────────────────────

function read(path: string): string {
  return readFileSync(resolve(REPO_ROOT, path), "utf8");
}

function mirrorTables(): Set<string> {
  const source = read("scripts/db/mirror_market_snapshots_to_demo.js");
  const tables = new Set<string>();
  for (const name of ["LATEST_ONE", "PER_KEY", "HISTORY"]) {
    const block = source.split(`export const ${name} = [`)[1].split("];")[0];
    for (const m of block.matchAll(/table: "(\w+)"/g)) tables.add(m[1]);
  }
  return tables;
}

function newsfeedMirrorTables(): Set<string> {
  const source = read("scripts/db/mirror_newsfeed_to_demo.js");
  const upsert = /const UPSERT_SQL = `INSERT INTO (\w+)/.exec(source);
  return new Set(upsert ? [upsert[1]] : []);
}

function seedTables(): Set<string> {
  const source = read("scripts/db/demo_seed.py");
  return new Set([...source.matchAll(/INSERT OR REPLACE INTO (\w+)/g)].map((m) => m[1]));
}

function demoMigrationTables(): Set<string> {
  const dir = resolve(REPO_ROOT, "scripts/db/demo_migrations");
  const tables = new Set<string>();
  for (const file of readdirSync(dir).filter((f) => f.endsWith(".sql"))) {
    for (const m of readFileSync(join(dir, file), "utf8").matchAll(/CREATE TABLE IF NOT EXISTS (\w+)/g)) {
      tables.add(m[1]);
    }
  }
  return tables;
}

// Tables a demo route reads that nothing fills in the demo database, and why
// an empty table is the correct demo behaviour.
const LEGITIMATELY_EMPTY: Record<string, string> = {
  flow_analysis_snapshots:
    "Portfolio-derived; the market mirror purges it every run (PURGED_ACCOUNT_TABLES). GET serves the empty envelope.",
  executed_orders:
    "Not seeded; /api/orders overlays request-time fixture executions (buildDemoOrders) on the seeded open orders.",
  research_post_sources:
    "Private research is never copied to demo; /api/newsfeed/posts falls back to the legacy posts query.",
  research_feedback:
    "Operator research labels are never copied to demo; the posts query strips them for non-operators.",
  service_health:
    "The mirrors heartbeat into the PROD database; the route renders a synthetic turso-db row when empty.",
  llm_model_catalog: "The model picker falls back to the built-in DEFAULT_MODELS catalog.",
};

// ── assertions ──────────────────────────────────────────────────────────────

const DEMO_BRANCH = /principal\.kind === "demo"|resolveDemoOrderDecision\(/;

type Entry = { key: string; route: string; method: DemoHttpMethod; policy: DemoMethodPolicy; file: RouteFile };

function tablesOf(policy: DemoMethodPolicy): string[] {
  return "tables" in policy || "writes" in policy
    ? [...((policy as { tables?: readonly string[] }).tables ?? []), ...((policy as { writes?: readonly string[] }).writes ?? [])]
    : [];
}

describe("demo route policy", () => {
  const routes = collectRoutes();
  const byRoute = new Map(routes.map((r) => [r.route, r]));
  const entries: Entry[] = [];
  for (const [route, policies] of Object.entries(DEMO_ROUTE_POLICY)) {
    const file = byRoute.get(route);
    if (!file) continue;
    for (const [method, policy] of Object.entries(policies) as [DemoHttpMethod, DemoMethodPolicy][]) {
      entries.push({ key: `${method} ${route}`, route, method, policy, file });
    }
  }
  const of = (mode: DemoMethodPolicy["mode"]) => entries.filter((e) => e.policy.mode === mode);

  it("finds the route files to check", () => {
    expect(routes.length).toBeGreaterThan(100);
  });

  it("every route and exported method on disk has a demo decision (filesystem pin)", () => {
    const missing = routes.flatMap((r) =>
      r.methods.filter((m) => !DEMO_ROUTE_POLICY[r.route]?.[m]).map((m) => `${m} ${r.route}`),
    );
    expect(missing, "classify new routes in web/lib/demo/routePolicy.ts").toEqual([]);
  });

  it("has no stale entries", () => {
    const stale = Object.entries(DEMO_ROUTE_POLICY).flatMap(([route, policies]) => {
      const file = byRoute.get(route);
      if (!file) return [`route ${route}`];
      return Object.keys(policies)
        .filter((m) => !file.methods.includes(m as DemoHttpMethod))
        .map((m) => `${m} ${route}`);
    });
    expect(stale).toEqual([]);
  });

  it("fixture methods branch on the demo principal", () => {
    const missing = of("fixture")
      .filter((e) => !(e.policy as { pendingFixture?: true }).pendingFixture)
      .filter((e) => !DEMO_BRANCH.test(methodBody(e.file.source, e.method)))
      .map((e) => e.key);
    expect(missing).toEqual([]);
  });

  it("pendingFixture is dropped once the demo branch ships", () => {
    const landed = of("fixture")
      .filter((e) => (e.policy as { pendingFixture?: true }).pendingFixture)
      .filter((e) => DEMO_BRANCH.test(methodBody(e.file.source, e.method)))
      .map((e) => e.key);
    expect(landed, "remove pendingFixture from these entries").toEqual([]);
  });

  it("blocked methods carry the guard they claim", () => {
    const wrong: string[] = [];
    for (const e of of("blocked")) {
      const body = methodBody(e.file.source, e.method);
      const via = (e.policy as { via: string }).via;
      const ok =
        via === "operatorOnly"
          ? /operatorOnly: true/.test(body) && !/demoBlockadeRoute: true/.test(body)
          : via === "demoAdmin"
            ? /requireDemoAdmin\(/.test(body)
            : via === "setupMode"
              ? /isSetupMode\(/.test(body)
              : /demoBlockadeRoute: true/.test(body) && /demoOrderRefusal\(/.test(body);
      if (!ok) wrong.push(`${e.key} (${via})`);
    }
    expect(wrong).toEqual([]);
  });

  it("no other method is operator-only (a demo surface must not 403 by accident)", () => {
    const undeclared = entries
      .filter((e) => e.policy.mode !== "blocked")
      .filter((e) => {
        const body = methodBody(e.file.source, e.method);
        return /operatorOnly: true/.test(body) && !/demoBlockadeRoute: true/.test(body);
      })
      .map((e) => e.key);
    expect(undeclared).toEqual([]);
  });

  it("ai methods are metered by the shared tier-D budget", () => {
    const unmetered = of("ai")
      .filter((e) => classifyRateTier(e.method, e.route) !== "D")
      .map((e) => e.key);
    expect(unmetered).toEqual([]);
  });

  it("public methods are on a middleware public or probe allowlist", () => {
    const allowlists = read("web/lib/publicShareRoutes.ts") + read("web/middleware.ts");
    const unlisted = of("public").filter((e) => !allowlists.includes(`"${e.route}"`)).map((e) => e.key);
    expect(unlisted).toEqual([]);
  });

  it("declared tables, backend endpoints and external sources appear in the route's import graph", () => {
    const absent: string[] = [];
    for (const e of entries) {
      const text = importClosureText(e.file.file);
      for (const table of tablesOf(e.policy)) {
        if (!new RegExp(`\\b${table}\\b`).test(text)) absent.push(`${e.key}: table ${table}`);
      }
      const policy = e.policy as { endpoints?: readonly string[]; sources?: readonly string[] };
      for (const endpoint of policy.endpoints ?? []) {
        if (!text.includes(endpoint)) absent.push(`${e.key}: endpoint ${endpoint}`);
      }
      for (const source of policy.sources ?? []) {
        if (!text.includes(source)) absent.push(`${e.key}: source ${source}`);
      }
    }
    expect(absent).toEqual([]);
  });

  it("every demo Turso table a route touches is produced in the demo environment", () => {
    const produced = new Set<string>([
      ...mirrorTables(),
      ...newsfeedMirrorTables(),
      ...seedTables(),
      ...demoMigrationTables(),
      // Tables the demo user's own requests write (alerts, watchlist, ...).
      ...entries.flatMap((e) => [...((e.policy as { writes?: readonly string[] }).writes ?? [])]),
    ]);
    const unproduced = [
      ...new Set(entries.flatMap((e) => tablesOf(e.policy).map((t) => `${t} (${e.key})`))),
    ].filter((label) => {
      const table = label.split(" ")[0];
      return !produced.has(table) && !(table in LEGITIMATELY_EMPTY);
    });
    expect(unproduced, "mirror/seed the table, or allowlist it with a reason").toEqual([]);
  });

  it("the legitimately-empty allowlist is not stale", () => {
    const read = new Set(entries.flatMap((e) => tablesOf(e.policy)));
    const produced = new Set([...mirrorTables(), ...newsfeedMirrorTables(), ...seedTables(), ...demoMigrationTables()]);
    for (const table of Object.keys(LEGITIMATELY_EMPTY)) {
      expect(read.has(table), `${table} is no longer read by any demo route`).toBe(true);
      expect(produced.has(table), `${table} is now produced; drop it from the allowlist`).toBe(false);
    }
  });

  it("producer parsers find the sources they read", () => {
    expect(mirrorTables().has("scan_snapshots")).toBe(true);
    expect(newsfeedMirrorTables()).toEqual(new Set(["posts"]));
    expect(seedTables().has("portfolio_snapshots")).toBe(true);
    expect(demoMigrationTables().has("demo_ai_usage")).toBe(true);
  });
});
