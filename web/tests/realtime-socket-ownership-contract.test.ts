/**
 * Socket ownership contract.
 *
 * The realtime prices socket must be owned by RealtimePricesProvider in the
 * root Providers tree (which persists across App Router navigations), never by
 * the per-page WorkspaceShell (which remounts on every route change and would
 * close the socket, fetch a fresh ws-ticket and resync the snapshot — the
 * 2026-09-01 mobile page-change lag). Behavior pin:
 * web/tests/realtime-prices-navigation-persistence.test.tsx.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));
const source = (path: string) => readFileSync(resolve(WEB_ROOT, path), "utf8");

/** Every .ts/.tsx source file under the given web/ subtrees. */
function sourceFiles(...roots: string[]): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name === "node_modules" || name === "__tests__") continue;
        walk(full);
      } else if (/\.tsx?$/.test(name) && !/\.(test|spec)\.tsx?$/.test(name)) {
        out.push(full);
      }
    }
  };
  for (const root of roots) walk(resolve(WEB_ROOT, root));
  return out;
}

describe("realtime prices socket ownership", () => {
  it("WorkspaceShell does not own a prices socket — it publishes to the root provider", () => {
    const shell = source("components/WorkspaceShell.tsx");
    expect(shell).not.toMatch(/\busePrices\s*\(/);
    expect(shell).toContain("useRealtimePrices");
    expect(shell).toContain("publishSubscriptions");
  });

  it("usePrices has EXACTLY one production call site across components/ and lib/: RealtimePricesProvider", () => {
    // "Presence in one file" is not uniqueness — scan the whole production
    // surface so a second socket owner anywhere reds this contract.
    const callSites: string[] = [];
    for (const file of sourceFiles("components", "lib")) {
      const text = readFileSync(file, "utf8");
      // The definition site declares `function usePrices(`; strip declarations
      // so only genuine invocations count.
      const invocations = text.replace(/\bexport function usePrices\s*\(/g, "");
      if (/\busePrices\s*\(/.test(invocations)) {
        callSites.push(relative(WEB_ROOT, file));
      }
    }
    expect(callSites).toEqual(["lib/RealtimePricesContext.tsx"]);
  });

  it("Providers.tsx mounts RealtimePricesProvider on EVERY boot path — no early return may skip it", () => {
    // The keyless (no Clerk key) branch once returned <ThemeProvider> alone,
    // silently dropping the realtime tree. The provider mount must sit before
    // the first `return` in the component body so no branch can bypass it.
    const providers = source("components/Providers.tsx");
    const body = providers.slice(providers.indexOf("export default function Providers"));
    expect(body.length, "Providers component body must be found").toBeGreaterThan(0);
    const mountAt = body.indexOf("<RealtimePricesProvider>");
    const firstReturnAt = body.search(/\breturn\b/);
    expect(mountAt, "RealtimePricesProvider must be mounted in Providers").toBeGreaterThanOrEqual(0);
    expect(firstReturnAt).toBeGreaterThanOrEqual(0);
    expect(
      mountAt,
      "an early return before <RealtimePricesProvider> lets a boot path skip the realtime tree",
    ).toBeLessThan(firstReturnAt);
  });
});
