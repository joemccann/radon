/**
 * T-440 (P1): web/app/layout.tsx `authlessTestBypass` is the conjunction of
 * RADON_AUTHLESS_TEST === "1" AND Boolean(RADON_AUTHLESS_TEST_TOKEN) AND a
 * matching x-radon-authless-test header. It controls whether Providers drops
 * the Clerk realtime token getter (components/Providers.tsx ->
 * lib/RealtimeAuthContext.tsx). This test pins every term by evaluating the
 * REAL RootLayout under all 8 flag/token/header combinations and asserting
 * the exact boolean passed to <Providers authlessTestBypass={...}>.
 *
 * Dropping any single term of the conjunction in layout.tsx flips at least
 * one of the 8 expectations, so each term reds this file when removed.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type React from "react";

// Header store the mocked next/headers reads per test.
const headerStore = new Map<string, string>();

vi.mock("next/headers", () => ({
  headers: async () => ({
    get: (name: string) => headerStore.get(name.toLowerCase()) ?? null,
  }),
}));

// next/font/local cannot run outside the Next compiler; return the shape
// layout.tsx consumes (style.fontFamily + variable).
vi.mock("next/font/local", () => ({
  default: () => ({ style: { fontFamily: "test" }, variable: "--font-test" }),
}));

// Marker component so the element tree can be searched for the real prop
// value layout passes. Nothing is rendered; the tree is inspected as data.
vi.mock("@/components/Providers", () => ({
  default: function ProvidersMarker() {
    return null;
  },
}));
vi.mock("@/components/PwaRegister", () => ({ default: () => null }));
vi.mock("@/components/ThemeBootstrap", () => ({ default: () => null }));

type AnyElement = {
  type?: unknown;
  props?: { children?: unknown; authlessTestBypass?: unknown } & Record<string, unknown>;
};

function findProviders(node: unknown, marker: unknown): AnyElement | null {
  if (!node || typeof node !== "object") return null;
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = findProviders(child, marker);
      if (hit) return hit;
    }
    return null;
  }
  const el = node as AnyElement;
  if (el.type === marker) return el;
  return findProviders(el.props?.children, marker);
}

const TOKEN = "authless-test-token-value";

async function computeBypass(opts: {
  flag?: string;
  token?: string;
  header?: string;
}): Promise<boolean> {
  vi.resetModules();
  headerStore.clear();
  delete process.env.RADON_AUTHLESS_TEST;
  delete process.env.RADON_AUTHLESS_TEST_TOKEN;
  if (opts.flag !== undefined) process.env.RADON_AUTHLESS_TEST = opts.flag;
  if (opts.token !== undefined) process.env.RADON_AUTHLESS_TEST_TOKEN = opts.token;
  if (opts.header !== undefined) headerStore.set("x-radon-authless-test", opts.header);

  const layout = await import("../app/layout");
  const providersModule = await import("@/components/Providers");
  const tree = await layout.default({ children: null as unknown as React.ReactNode });
  const providers = findProviders(tree, providersModule.default);
  expect(providers, "Providers element must be present in the layout tree").toBeTruthy();
  const value = providers!.props!.authlessTestBypass;
  expect(typeof value, "authlessTestBypass must be an explicit boolean").toBe("boolean");
  return value as boolean;
}

describe("T-440: layout authlessTestBypass is a strict three-term conjunction", () => {
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    delete process.env.RADON_AUTHLESS_TEST;
    delete process.env.RADON_AUTHLESS_TEST_TOKEN;
    headerStore.clear();
  });

  it("flag=1, token set, header matches -> true (the only true cell)", async () => {
    expect(await computeBypass({ flag: "1", token: TOKEN, header: TOKEN })).toBe(true);
  });

  it("flag unset, token set, header matches -> false", async () => {
    expect(await computeBypass({ token: TOKEN, header: TOKEN })).toBe(false);
  });

  it("flag=1, token unset, header present -> false", async () => {
    expect(await computeBypass({ flag: "1", header: TOKEN })).toBe(false);
  });

  it("flag=1, token set, header absent -> false", async () => {
    expect(await computeBypass({ flag: "1", token: TOKEN })).toBe(false);
  });

  it("flag=1, token set, header MISMATCH -> false", async () => {
    expect(await computeBypass({ flag: "1", token: TOKEN, header: "wrong" })).toBe(false);
  });

  it("flag unset, token unset, header absent -> false", async () => {
    expect(await computeBypass({})).toBe(false);
  });

  it("flag unset, token set, header absent -> false", async () => {
    expect(await computeBypass({ token: TOKEN })).toBe(false);
  });

  it("flag unset, token unset, header present -> false", async () => {
    expect(await computeBypass({ header: TOKEN })).toBe(false);
  });

  it("flag=0 (non-'1' value) with token and matching header -> false", async () => {
    expect(await computeBypass({ flag: "0", token: TOKEN, header: TOKEN })).toBe(false);
  });

  it("empty-string token with empty header does not slip through Boolean(token)", async () => {
    expect(await computeBypass({ flag: "1", token: "", header: "" })).toBe(false);
  });
});
