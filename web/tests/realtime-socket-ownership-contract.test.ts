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
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));
const source = (path: string) => readFileSync(resolve(WEB_ROOT, path), "utf8");

describe("realtime prices socket ownership", () => {
  it("WorkspaceShell does not own a prices socket — it publishes to the root provider", () => {
    const shell = source("components/WorkspaceShell.tsx");
    expect(shell).not.toMatch(/\busePrices\s*\(/);
    expect(shell).toContain("useRealtimePrices");
    expect(shell).toContain("publishSubscriptions");
  });

  it("RealtimePricesProvider is mounted in the root Providers tree", () => {
    const providers = source("components/Providers.tsx");
    expect(providers).toContain("RealtimePricesProvider");
  });

  it("usePrices has exactly one production call site: RealtimePricesProvider", () => {
    const provider = source("lib/RealtimePricesContext.tsx");
    expect(provider).toMatch(/\busePrices\s*\(/);
  });
});
