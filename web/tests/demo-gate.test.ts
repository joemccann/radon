import { describe, it, expect, vi } from "vitest";
import { NextRequest } from "next/server";
import { handleDemoGate } from "@/lib/demo/demoGate";
import type { DemoRateLimitResult } from "@/lib/demo/rateLimit";

const NOW = Date.parse("2026-06-25T12:00:00-04:00");
const FUTURE = "2026-06-29T16:00:00-04:00";
const PAST = "2026-06-24T16:00:00-04:00";

const activeMeta = { demoRole: "trial", demoTrialExpiresAt: FUTURE };
const expiredMeta = { demoRole: "trial", demoTrialExpiresAt: PAST };

const allow: DemoRateLimitResult = { success: true, limit: 100, remaining: 99, reset: 0 };
const deny: DemoRateLimitResult = { success: false, limit: 5, remaining: 0, reset: NOW + 2000 };

function apiReq(path = "/api/portfolio", method = "GET") {
  return new NextRequest(`https://demo.radon.run${path}`, { method });
}

describe("handleDemoGate", () => {
  it("non-demo user passes through (null)", async () => {
    const res = await handleDemoGate(
      { userId: "u", metadata: null, request: apiReq() },
      { now: NOW, rateLimiter: vi.fn() },
    );
    expect(res).toBeNull();
  });

  it("denies missing role metadata on the demo deployment", async () => {
    const res = await handleDemoGate(
      { userId: "pending", metadata: null, request: apiReq() },
      { now: NOW, demoDeployment: true },
    );
    expect(res?.status).toBe(403);
    expect((await res!.json()).code).toBe("DEMO_ACCESS_PENDING");
  });

  it.each([
    ["demoRole pending", { demoRole: "pending" }],
    ["trial with no expiry yet", { demoRole: "trial" }],
  ])("%s: page goes to /demo-pending, not /trial-expired", async (_label, metadata) => {
    const res = await handleDemoGate(
      { userId: "u", metadata, request: new NextRequest("https://demo.radon.run/portfolio") },
      { now: NOW, rateLimiter: async () => allow, demoDeployment: true },
    );
    expect(res?.status).toBe(307);
    expect(new URL(res!.headers.get("location")!).pathname).toBe("/demo-pending");
  });

  it.each([
    ["demoRole pending", { demoRole: "pending" }],
    ["trial with no expiry yet", { demoRole: "trial" }],
  ])("%s: API stays 403 DEMO_ACCESS_PENDING", async (_label, metadata) => {
    const limiter = vi.fn(async () => allow);
    const res = await handleDemoGate(
      { userId: "u", metadata, request: apiReq() },
      { now: NOW, rateLimiter: limiter, demoDeployment: true },
    );
    expect(res?.status).toBe(403);
    expect((await res!.json()).code).toBe("DEMO_ACCESS_PENDING");
    expect(limiter).not.toHaveBeenCalled();
  });

  it("active demo user under limit passes through", async () => {
    const res = await handleDemoGate(
      { userId: "u", metadata: activeMeta, request: apiReq() },
      { now: NOW, rateLimiter: async () => allow },
    );
    expect(res).toBeNull();
  });

  it("expired demo user gets 403 on an API path", async () => {
    const res = await handleDemoGate(
      { userId: "u", metadata: expiredMeta, request: apiReq() },
      { now: NOW, rateLimiter: async () => allow },
    );
    expect(res?.status).toBe(403);
    const body = await res!.json();
    expect(body.code).toBe("DEMO_TRIAL_EXPIRED");
  });

  it("expired demo user is redirected to the trial-expired page on a page path", async () => {
    const res = await handleDemoGate(
      { userId: "u", metadata: expiredMeta, request: new NextRequest("https://demo.radon.run/portfolio") },
      { now: NOW, rateLimiter: async () => allow },
    );
    expect(res?.headers.get("location")).toContain("/trial-expired");
  });

  it("rate-limited demo API request gets 429 + Retry-After", async () => {
    const res = await handleDemoGate(
      { userId: "u", metadata: activeMeta, request: apiReq("/api/orders/place", "POST") },
      { now: NOW, rateLimiter: async () => deny },
    );
    expect(res?.status).toBe(429);
    expect(res?.headers.get("Retry-After")).toBe("2");
  });

  it("a batch fan-out read consumes its own hourly and daily ceilings", async () => {
    // Not the ordinary-read pair (A/K): one request reaches up to 30 symbols.
    // Not the producer pair (B/L) either, whose daily bucket is shared with
    // every scan, so automatic price backfill cannot lock operator controls
    // out for the rest of the day.
    const limiter = vi.fn(async () => allow);
    const res = await handleDemoGate(
      { userId: "u", metadata: activeMeta, request: apiReq("/api/previous-close", "POST") },
      { now: NOW, rateLimiter: limiter },
    );
    expect(res).toBeNull();
    expect(limiter.mock.calls.map(([tier]) => tier)).toEqual(["M", "N"]);
  });

  it("a denied daily ceiling carries Retry-After like every other 429", async () => {
    // Without it the client falls back to its maximum backoff and re-polls
    // for the rest of the day instead of waiting out the window.
    const limiter = vi.fn(async (tier: string) =>
      tier === "K" ? deny : allow);
    const res = await handleDemoGate(
      { userId: "u", metadata: activeMeta, request: apiReq("/api/blotter") },
      { now: NOW, rateLimiter: limiter },
    );
    expect(res?.status).toBe(429);
    expect((await res!.json()).tier).toBe("K");
    expect(res?.headers.get("Retry-After")).toBe("2");
  });

  it("does NOT rate-limit page navigations (only /api/*)", async () => {
    const limiter = vi.fn(async () => deny);
    const res = await handleDemoGate(
      { userId: "u", metadata: activeMeta, request: new NextRequest("https://demo.radon.run/portfolio") },
      { now: NOW, rateLimiter: limiter },
    );
    expect(res).toBeNull();
    expect(limiter).not.toHaveBeenCalled();
  });

  it("websocket ticket reconnects consume minute and daily ceilings", async () => {
    const limiter = vi.fn(async () => allow);
    const res = await handleDemoGate(
      { userId: "u", metadata: activeMeta, request: apiReq("/api/ib/ws-ticket", "POST") },
      { now: NOW, rateLimiter: limiter },
    );
    expect(res).toBeNull();
    expect(limiter.mock.calls.map(([tier]) => tier)).toEqual(["E", "F"]);
  });

  it("headline polling consumes isolated minute and daily ceilings", async () => {
    const limiter = vi.fn(async () => allow);
    const res = await handleDemoGate(
      { userId: "u", metadata: activeMeta, request: apiReq("/api/headlines") },
      { now: NOW, rateLimiter: limiter },
    );
    expect(res).toBeNull();
    expect(limiter.mock.calls.map(([tier]) => tier)).toEqual(["G", "H"]);
  });

  it("keeps a first scanner visit out of an exhausted regime budget", async () => {
    const usage = new Map<string, number>();
    const limiter = vi.fn(async (tier: string, key: string): Promise<DemoRateLimitResult> => {
      // Intent: per-resource tier-A isolation. The K daily backstop (REL-244)
      // is exercised elsewhere; keep it open so it does not mask the A keys.
      if (tier !== "A") return { success: true, limit: 1000, remaining: 999, reset: 0 };
      const counterKey = `${tier}:${key}`;
      const next = (usage.get(counterKey) ?? 0) + 1;
      usage.set(counterKey, next);
      return {
        success: next <= 10,
        limit: 10,
        remaining: Math.max(0, 10 - next),
        reset: NOW + 60_000,
      };
    });

    for (let requestNumber = 0; requestNumber < 10; requestNumber += 1) {
      const res = await handleDemoGate(
        { userId: "fresh-demo-user", metadata: activeMeta, request: apiReq("/api/regime") },
        { now: NOW, rateLimiter: limiter },
      );
      expect(res).toBeNull();
    }

    const scanner = await handleDemoGate(
      { userId: "fresh-demo-user", metadata: activeMeta, request: apiReq("/api/scanner") },
      { now: NOW, rateLimiter: limiter },
    );

    expect(scanner).toBeNull();
    expect(limiter.mock.calls.filter(([tier]) => tier === "A").at(-1)).toEqual([
      "A",
      "fresh-demo-user:resource:scanner",
    ]);
  });

  it("shares one abuse budget across nested routes for the same resource", async () => {
    const limiter = vi.fn(async () => allow);

    await handleDemoGate(
      { userId: "user", metadata: activeMeta, request: apiReq("/api/scanner") },
      { now: NOW, rateLimiter: limiter },
    );
    await handleDemoGate(
      { userId: "user", metadata: activeMeta, request: apiReq("/api/scanner/theta") },
      { now: NOW, rateLimiter: limiter },
    );

    // Each read also charges the user-global K daily backstop (REL-244).
    expect(limiter.mock.calls).toEqual([
      ["A", "user:resource:scanner"],
      ["K", "user"],
      ["A", "user:resource:scanner"],
      ["K", "user"],
    ]);
  });

  it("shell polling consumes isolated minute and daily ceilings", async () => {
    const limiter = vi.fn(async () => allow);
    const res = await handleDemoGate(
      { userId: "u", metadata: activeMeta, request: apiReq("/api/futures-quote") },
      { now: NOW, rateLimiter: limiter },
    );
    expect(res).toBeNull();
    expect(limiter.mock.calls.map(([tier]) => tier)).toEqual(["I", "J"]);
  });
});
