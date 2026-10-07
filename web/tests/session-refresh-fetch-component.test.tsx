// @vitest-environment jsdom
//
// The interceptor only helps if it is actually over window.fetch, refreshes
// through Clerk with skipCache (the cached token is the expired one), and is
// mounted in the Clerk branch of Providers.

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";

const getToken = vi.fn(async (_opts?: { skipCache?: boolean }) => "fresh-jwt");
vi.mock("@clerk/nextjs", () => ({ useAuth: () => ({ getToken }) }));

import SessionRefreshFetch from "@/components/SessionRefreshFetch";

const calls: Array<{ url: string; method: string }> = [];
let first = true;
const network = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  calls.push({ url: String(input), method: init?.method ?? "GET" });
  if (first) {
    first = false;
    return new Response("{}", { status: 401, headers: { "x-clerk-auth-status": "signed-out" } });
  }
  return new Response("{\"ok\":true}", { status: 200 });
});

beforeEach(() => {
  calls.length = 0;
  first = true;
  getToken.mockClear();
  vi.stubGlobal("fetch", network);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SessionRefreshFetch", () => {
  it("wraps window.fetch, refreshes via Clerk with skipCache, and restores on unmount", async () => {
    const { unmount } = render(<SessionRefreshFetch />);
    await waitFor(() => expect(window.fetch).not.toBe(network));

    const res = await window.fetch("/api/orders", { method: "POST" });
    expect(res.status).toBe(200);
    expect(getToken).toHaveBeenCalledWith({ skipCache: true });
    expect(calls).toEqual([
      { url: "/api/orders", method: "POST" },
      { url: "/api/orders", method: "POST" },
    ]);

    unmount();
    expect(window.fetch).toBe(network);
  });

  it("is mounted in the Clerk branch of Providers, ahead of the data providers", () => {
    const src = readFileSync(join(__dirname, "../components/Providers.tsx"), "utf8");
    const bridge = src.indexOf("<ClerkThemeBridge>");
    const mount = src.indexOf("<SessionRefreshFetch />");
    expect(bridge).toBeGreaterThan(-1);
    expect(mount).toBeGreaterThan(bridge);
    expect(mount).toBeLessThan(src.indexOf("{core}", bridge));
  });
});
