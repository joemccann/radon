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
    expect(limiter.mock.calls.at(-1)).toEqual([
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

    expect(limiter.mock.calls).toEqual([
      ["A", "user:resource:scanner"],
      ["A", "user:resource:scanner"],
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
