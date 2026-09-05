// REL-244 (R-652 + R-661): demo rate-budget hardening.
//
// R-652 — `demoRateLimitKey` minted a fresh Upstash sliding-window key per
// FIRST path segment of the RAW pathname, pre-routing. `/api/aaa1`,
// `/api/aaa2`, ... each received a fresh tier-A allowance (100/hr), so one
// trial account could mint unbounded budgets from nonsense segments; and
// tiers A/B had no daily ceiling (the backstop mapped only E-J).
// R-661 — /api/trin and /api/dispersion called requireRouteAccess() bare,
// so demo traffic on them never consumed the durable demo budget.
import { readdirSync, statSync } from "node:fs";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join, resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { NextRequest } from "next/server";
import { handleDemoGate } from "@/lib/demo/demoGate";
import { demoRateLimitKey, KNOWN_API_SEGMENTS } from "@/lib/demo/rateTier";
import type { DemoRateLimitResult } from "@/lib/demo/rateLimit";

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url));
const NOW = Date.parse("2026-06-25T12:00:00-04:00");
const activeMeta = { demoRole: "trial", demoTrialExpiresAt: "2026-06-29T16:00:00-04:00" };
const allow: DemoRateLimitResult = { success: true, limit: 100, remaining: 99, reset: 0 };

function apiReq(path: string, method = "GET") {
  return new NextRequest(`https://demo.radon.run${path}`, { method });
}

describe("R-652: unknown segments collapse to one shared bucket", () => {
  it("keys all unknown first segments to the same bucket", () => {
    const a = demoRateLimitKey("A", "user", "/api/aaa1");
    const b = demoRateLimitKey("A", "user", "/api/aaa2");
    expect(a).toBe(b);
    // Known resources keep their per-resource isolation.
    expect(demoRateLimitKey("A", "user", "/api/scanner")).toBe("user:resource:scanner");
    expect(demoRateLimitKey("A", "user", "/api/regime")).not.toBe(a);
  });

  it("the allowlist covers every real /api first segment (drift pin)", () => {
    const apiDir = resolve(WEB_ROOT, "app/api");
    const segments = readdirSync(apiDir).filter((entry) =>
      statSync(join(apiDir, entry)).isDirectory(),
    );
    const missing = segments.filter((segment) => !KNOWN_API_SEGMENTS.has(segment));
    expect(missing, "new /api segments must be added to KNOWN_API_SEGMENTS").toEqual([]);
  });

  it("50 nonsense segments trip one shared tier-A window", async () => {
    const usage = new Map<string, number>();
    const limiter = vi.fn(async (tier: string, key: string): Promise<DemoRateLimitResult> => {
      const counterKey = `${tier}:${key}`;
      const next = (usage.get(counterKey) ?? 0) + 1;
      usage.set(counterKey, next);
      return { success: next <= 100, limit: 100, remaining: Math.max(0, 100 - next), reset: NOW + 60_000 };
    });
    let tripped = false;
    for (let segment = 0; segment < 50 && !tripped; segment += 1) {
      for (let hit = 0; hit < 3; hit += 1) {
        const res = await handleDemoGate(
          { userId: "abuser", metadata: activeMeta, request: apiReq(`/api/aaa${segment}/x`) },
          { now: NOW, rateLimiter: limiter },
        );
        if (res?.status === 429) { tripped = true; break; }
      }
    }
    expect(tripped, "150 requests across nonsense segments must exhaust one shared 100-request bucket").toBe(true);
  });
});

describe("R-652: tiers A/B gain a daily backstop", () => {
  it("tier-A reads consume a user-global daily ceiling", async () => {
    const limiter = vi.fn(async (tier: string): Promise<DemoRateLimitResult> =>
      tier === "A" ? allow : { success: false, limit: 1000, remaining: 0, reset: NOW + 2000 },
    );
    const res = await handleDemoGate(
      { userId: "u", metadata: activeMeta, request: apiReq("/api/blotter") },
      { now: NOW, rateLimiter: limiter },
    );
    expect(res?.status).toBe(429);
    // Refused across segments: a different resource is refused by the same
    // user-global daily key.
    const other = await handleDemoGate(
      { userId: "u", metadata: activeMeta, request: apiReq("/api/streaks") },
      { now: NOW, rateLimiter: limiter },
    );
    expect(other?.status).toBe(429);
    const dailyCalls = limiter.mock.calls.filter(([tier]) => tier !== "A");
    expect(dailyCalls.length).toBeGreaterThan(0);
    for (const [, key] of dailyCalls) expect(key).toBe("u");
  });

  it("tier-B producers consume a user-global daily ceiling", async () => {
    const limiter = vi.fn(async (tier: string): Promise<DemoRateLimitResult> =>
      tier === "B" ? allow : { success: false, limit: 50, remaining: 0, reset: NOW + 2000 },
    );
    const res = await handleDemoGate(
      { userId: "u", metadata: activeMeta, request: apiReq("/api/vcg/scan", "POST") },
      { now: NOW, rateLimiter: limiter },
    );
    expect(res?.status).toBe(429);
  });

  it("a passing daily backstop still lets tier-A requests through", async () => {
    const limiter = vi.fn(async () => allow);
    const res = await handleDemoGate(
      { userId: "u", metadata: activeMeta, request: apiReq("/api/blotter") },
      { now: NOW, rateLimiter: limiter },
    );
    expect(res).toBeNull();
    expect(limiter.mock.calls.length).toBe(2);
  });
});

describe("R-661: trin and dispersion charge the durable demo budget", () => {
  for (const route of ["trin", "dispersion"]) {
    it(`${route} passes rate + durableRateTier to requireRouteAccess`, () => {
      const text = readFileSync(resolve(WEB_ROOT, `app/api/${route}/route.ts`), "utf8");
      expect(text, route).toContain(`rate: { key: "${route}:route"`);
      expect(text, route).toContain('durableRateTier: "A"');
    });
  }
});
