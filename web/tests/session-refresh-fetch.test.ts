// @vitest-environment jsdom
//
// iOS freezes the PWA in the background, so Clerk's 60s session JWT goes stale.
// On resume Clerk-js refreshes on `visibilitychange`, but the app's own resume
// polls fire in the same tick and reach the middleware with the expired cookie:
// a POST is never refresh-eligible server-side, so Clerk answers signed-out and
// middleware.ts returns 401 before any route handler runs. The UI rendered that
// as "Your session has expired. Sign in again" while the session was fine
// (prod Caddy log 2026-10-06 14:00:48Z: six iPhone 401s, reason
// session-token-expired-*, then 200s one second later).
//
// The interceptor refreshes the Clerk token once and replays ONLY a request
// the middleware provably short-circuited (x-clerk-auth-status: signed-out).

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  installSessionRefreshFetch,
  isMiddlewareSignedOut,
} from "@/lib/sessionRefreshFetch";

type Call = { url: string; method: string; body: string };

function signedOut401(): Response {
  return new Response(JSON.stringify({ error: "Unauthorized", code: "UNAUTHORIZED" }), {
    status: 401,
    headers: {
      "Content-Type": "application/json",
      "x-clerk-auth-status": "signed-out",
      "x-clerk-auth-reason": "session-token-expired-refresh-non-eligible-non-get",
    },
  });
}

function ok(body: unknown = { ok: true }): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function harness(responses: Array<() => Response>, token: string | null = "fresh") {
  const calls: Call[] = [];
  let i = 0;
  const base = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), method: init?.method ?? "GET", body: String(init?.body ?? "") });
    const next = responses[Math.min(i, responses.length - 1)];
    i += 1;
    return next();
  });
  const target = { fetch: base as unknown as typeof fetch };
  const refresh = vi.fn(async () => token);
  const uninstall = installSessionRefreshFetch(target, refresh);
  return { target, calls, refresh, uninstall, base };
}

afterEach(() => vi.restoreAllMocks());

describe("isMiddlewareSignedOut", () => {
  it("is true only for a 401 the Clerk middleware marked signed-out", () => {
    expect(isMiddlewareSignedOut(signedOut401())).toBe(true);
    expect(isMiddlewareSignedOut(new Response("{}", { status: 401 }))).toBe(false);
    expect(isMiddlewareSignedOut(new Response("{}", {
      status: 401, headers: { "x-clerk-auth-status": "signed-in" },
    }))).toBe(false);
    expect(isMiddlewareSignedOut(new Response("{}", {
      status: 403, headers: { "x-clerk-auth-status": "signed-out" },
    }))).toBe(false);
  });
});

describe("installSessionRefreshFetch", () => {
  it("refreshes the session and replays a middleware-rejected POST exactly once", async () => {
    const h = harness([signedOut401, () => ok({ synced: true })]);
    const res = await h.target.fetch("/api/portfolio", { method: "POST", body: "{\"a\":1}" });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ synced: true });
    expect(h.refresh).toHaveBeenCalledTimes(1);
    expect(h.calls).toEqual([
      { url: "/api/portfolio", method: "POST", body: "{\"a\":1}" },
      { url: "/api/portfolio", method: "POST", body: "{\"a\":1}" },
    ]);
  });

  it("returns the 401 untouched when there is no live Clerk session to refresh", async () => {
    const h = harness([signedOut401, () => ok()], null);
    const res = await h.target.fetch("/api/orders");
    expect(res.status).toBe(401);
    expect(h.calls).toHaveLength(1);
  });

  it("returns the 401 untouched when the refresh itself throws", async () => {
    const h = harness([signedOut401, () => ok()]);
    h.refresh.mockRejectedValueOnce(new Error("offline"));
    const res = await h.target.fetch("/api/orders");
    expect(res.status).toBe(401);
    expect(h.calls).toHaveLength(1);
  });

  it("never replays a 401 the route handler produced (session was valid at the middleware)", async () => {
    const routeLevel = () => new Response("{}", { status: 401, headers: { "x-clerk-auth-status": "signed-in" } });
    const h = harness([routeLevel, () => ok()]);
    const res = await h.target.fetch("/api/orders/place", { method: "POST", body: "{}" });
    expect(res.status).toBe(401);
    expect(h.refresh).not.toHaveBeenCalled();
    expect(h.calls).toHaveLength(1);
  });

  it("returns the 401 when the refresh never settles (Clerk-js failed to load)", async () => {
    const calls: string[] = [];
    const target = {
      fetch: (async (input: RequestInfo | URL) => { calls.push(String(input)); return signedOut401(); }) as typeof fetch,
    };
    installSessionRefreshFetch(target, () => new Promise<string>(() => {}), 20);
    const res = await target.fetch("/api/orders");
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(1);
  });

  it("replays at most once even if the replay is rejected again", async () => {
    const h = harness([signedOut401, signedOut401, () => ok()]);
    const res = await h.target.fetch("/api/orders");
    expect(res.status).toBe(401);
    expect(h.calls).toHaveLength(2);
  });

  it("leaves non-API and cross-origin requests alone", async () => {
    const h = harness([signedOut401]);
    expect((await h.target.fetch("/sign-in")).status).toBe(401);
    expect((await h.target.fetch("https://example.com/api/orders")).status).toBe(401);
    expect(h.refresh).not.toHaveBeenCalled();
    expect(h.calls).toHaveLength(2);
  });

  it("replays an absolute same-origin API URL", async () => {
    const h = harness([signedOut401, () => ok()]);
    const res = await h.target.fetch(new URL("/api/orders", window.location.origin));
    expect(res.status).toBe(200);
    expect(h.calls).toHaveLength(2);
  });

  it("does not replay a Request object or a streamed body it cannot resend", async () => {
    const h = harness([signedOut401]);
    expect((await h.target.fetch(new Request(`${window.location.origin}/api/orders`))).status).toBe(401);
    const stream = new ReadableStream({ start(c) { c.close(); } });
    expect((await h.target.fetch("/api/orders", { method: "POST", body: stream })).status).toBe(401);
    expect(h.refresh).not.toHaveBeenCalled();
  });

  it("shares one refresh across the resume burst of concurrent 401s", async () => {
    let release!: (t: string) => void;
    const h = harness([signedOut401, signedOut401, signedOut401, () => ok(), () => ok(), () => ok()]);
    h.refresh.mockImplementation(() => new Promise<string>((r) => { release = r; }));
    const burst = Promise.all([
      h.target.fetch("/api/portfolio"),
      h.target.fetch("/api/orders"),
      h.target.fetch("/api/service-health"),
    ]);
    await vi.waitFor(() => expect(h.refresh).toHaveBeenCalled());
    expect(h.calls).toHaveLength(3);
    release("fresh");
    const results = await burst;
    expect(results.map((r) => r.status)).toEqual([200, 200, 200]);
    expect(h.refresh).toHaveBeenCalledTimes(1);
  });

  it("calls the original fetch with its own receiver and restores it on uninstall", async () => {
    const receivers: unknown[] = [];
    const target: { fetch: typeof fetch } = {
      fetch: function (this: unknown) { receivers.push(this); return Promise.resolve(ok()); } as unknown as typeof fetch,
    };
    const original = target.fetch;
    const uninstall = installSessionRefreshFetch(target, async () => "t");
    expect(target.fetch).not.toBe(original);
    await target.fetch("/api/orders");
    expect(receivers[0]).toBe(target);
    uninstall();
    expect(target.fetch).toBe(original);
  });
});
