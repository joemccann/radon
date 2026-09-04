/**
 * @vitest-environment jsdom
 *
 * Socket ownership contract.
 *
 * The realtime prices socket must be owned by RealtimePricesProvider in the
 * root Providers tree (which persists across App Router navigations), never by
 * the per-page WorkspaceShell (which remounts on every route change and would
 * close the socket, fetch a fresh ws-ticket and resync the snapshot — the
 * 2026-09-01 mobile page-change lag). Behavior pin:
 * web/tests/realtime-prices-navigation-persistence.test.tsx.
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { cleanup, render } from "@testing-library/react";

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("@clerk/nextjs", () => ({
  ClerkProvider: ({ children }: { children: unknown }) => children,
  useAuth: () => ({ isLoaded: true, isSignedIn: false, userId: null, getToken: async () => null }),
}));
vi.mock("@clerk/themes", () => ({ dark: {} }));

// jsdom lacks matchMedia (ThemeContext) and WebSocket (the prices socket);
// no network or socket is exercised by this contract.
if (typeof window !== "undefined") {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia);
  (globalThis as { WebSocket?: unknown }).WebSocket = class {
    close() {}
    send() {}
  };
  globalThis.fetch = (() => new Promise(() => {})) as typeof fetch;
}

const ORIGINAL_CLERK_KEY = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
afterEach(() => {
  cleanup();
  if (ORIGINAL_CLERK_KEY === undefined) delete process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  else process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = ORIGINAL_CLERK_KEY;
});

// jsdom rewrites import.meta.url to a non-file scheme, so walk up from cwd
// to the checkout root instead.
const WEB_ROOT = (() => {
  let dir = process.cwd();
  for (let i = 0; i < 6; i += 1) {
    if (existsSync(join(dir, "web", "components", "Providers.tsx"))) return join(dir, "web");
    if (existsSync(join(dir, "components", "Providers.tsx"))) return dir;
    dir = resolve(dir, "..");
  }
  throw new Error(`could not locate web/ from ${process.cwd()}`);
})();
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

  // Behavioural pin (T-409). The old version of this case compared the TEXT
  // OFFSET of "<RealtimePricesProvider>" against the first `return` in the
  // file. Once the tree moved into a `const core` above both returns that
  // comparison was unconditionally true, so the exact T-389 regression
  // (keyless branch returning <ThemeProvider>{children}</ThemeProvider>)
  // stayed green. Render both boot paths instead and read the context.
  for (const [label, key] of [
    ["Clerk configured", "pk_test_ownership_stub"],
    ["no Clerk publishable key (first-run setup)", ""],
  ] as const) {
    it(`Providers mounts RealtimePricesProvider on the ${label} boot path`, async () => {
      vi.resetModules();
      if (key) process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = key;
      else delete process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

      const { useRealtimePrices } = await import("@/lib/RealtimePricesContext");
      const { default: Providers } = await import("@/components/Providers");

      const seen: unknown[] = [];
      const Probe = () => {
        seen.push(useRealtimePrices());
        return null;
      };

      // Baseline: the same module instance's inert DEFAULT_VALUE, read with
      // no provider above it. Identity comparison against it is what proves a
      // real provider mounted rather than the tree being silently dropped.
      render(createElement(Probe));
      const fallback = seen.pop() as { publishSubscriptions: unknown };
      cleanup();

      render(createElement(Providers, null, createElement(Probe)));
      const value = seen.pop() as { publishSubscriptions: unknown };
      cleanup();

      expect(value, `${label}: no RealtimePricesProvider above the tree`).not.toBe(fallback);
      expect(
        value.publishSubscriptions,
        `${label}: consumers got the inert default publishSubscriptions`,
      ).not.toBe(fallback.publishSubscriptions);
    });
  }
});
