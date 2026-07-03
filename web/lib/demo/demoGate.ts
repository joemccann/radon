// Middleware gate for demo trial users (demo.radon.run, Phase 2 + Phase 5).
//
// Runs in the Edge runtime — NO node:* imports (see
// feedback_middleware_edge_runtime). Two responsibilities, both scoped to
// users carrying `demoRole` in their Clerk metadata; everyone else passes
// through untouched (resolveDemoContext → null):
//
//   1. Trial expiry — an expired demo user is blocked everywhere (403 JSON for
//      /api/*, redirect to the public /trial-expired page which forwards on
//      to radon.run).
//   2. Tiered rate-limit — Upstash sliding-window per Clerk userId, applied to
//      /api/* only (page navigation isn't the DOS surface, and tier-A budgets
//      would catch ordinary clicking). No-ops when Upstash is unconfigured.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { resolveDemoContext, type DemoPublicMetadata } from "./demoRole";
import { classifyRateTier } from "./rateTier";
import { demoRateLimit, type DemoRateLimitResult, type DemoRateTier } from "./rateLimit";

function isApiPath(pathname: string): boolean {
  return pathname === "/api" || pathname.startsWith("/api/");
}

function noStore(response: NextResponse): NextResponse {
  response.headers.set("Cache-Control", "no-store, no-cache, must-revalidate");
  return response;
}

export type DemoGateDeps = {
  rateLimiter?: (tier: DemoRateTier, userId: string) => Promise<DemoRateLimitResult>;
  now?: number;
};

/**
 * Gate a request for a (possibly) demo user.
 * @returns a `NextResponse` to short-circuit, or `null` to continue.
 */
export async function handleDemoGate(
  params: {
    userId: string;
    metadata: DemoPublicMetadata | null | undefined;
    request: NextRequest;
  },
  deps: DemoGateDeps = {},
): Promise<NextResponse | null> {
  const { userId, metadata, request } = params;
  const ctx = resolveDemoContext(metadata, deps.now);
  if (!ctx) return null; // not a demo user — no demo gating

  const pathname = request.nextUrl.pathname;
  const api = isApiPath(pathname);

  if (ctx.expired) {
    if (api) {
      return noStore(
        NextResponse.json(
          {
            error: "Your demo trial has expired.",
            code: "DEMO_TRIAL_EXPIRED",
            trialExpiresAt: ctx.trialExpiresAt,
          },
          { status: 403 },
        ),
      );
    }
    // Public page (middleware isPublicRoute) — sign-in itself bounces
    // signed-in users back to /portfolio, which would loop forever here.
    const url = new URL("/trial-expired", request.url);
    return NextResponse.redirect(url);
  }

  // Rate-limit /api/* only.
  if (api) {
    const tier = classifyRateTier(request.method, pathname);
    const limiter = deps.rateLimiter ?? demoRateLimit;
    const rl = await limiter(tier, userId);
    if (!rl.success) {
      const res = NextResponse.json(
        {
          error: "Demo rate limit exceeded. Slow down.",
          code: "DEMO_RATE_LIMITED",
          tier,
          limit: rl.limit,
          resetAt: rl.reset,
        },
        { status: 429 },
      );
      if (rl.reset) {
        const retryAfter = Math.max(0, Math.ceil((rl.reset - (deps.now ?? Date.now())) / 1000));
        res.headers.set("Retry-After", String(retryAfter));
      }
      return noStore(res);
    }
  }

  return null;
}
