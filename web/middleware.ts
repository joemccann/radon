import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { isAuthorizedProbeRequest } from "@/lib/probeAuth";
import { handleDemoGate } from "@/lib/demo/demoGate";
import type { DemoPublicMetadata } from "@/lib/demo/demoRole";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

function isLocalHost(url: URL): boolean {
  return LOCAL_HOSTS.has(url.hostname);
}

// ── Enforced Content-Security-Policy (per-request nonce) ────────────────────
//
// Was a Report-Only header in next.config.mjs. Now ENFORCED and owned here so
// each HTML response carries a fresh nonce.
//
// script-src = 'self' + per-request nonce + the Clerk host allowlist. The nonce
// covers Next.js's inline + framework <script> tags and our own inline
// ThemeBootstrap. Clerk renders its loader (clerk.browser.js) as a STATIC
// <script src> WITHOUT a nonce and does not propagate the request nonce to it,
// so it must be admitted by host. We deliberately do NOT use 'strict-dynamic'
// here: strict-dynamic makes host allowlists inert and would BLOCK Clerk's
// unnonced loader, breaking sign-in (verified 2026-06-29). nonce + tight host
// allowlist still removes 'unsafe-inline' and 'unsafe-eval' — the actual audit
// finding. (Future hardening: pass `nonce` to <ClerkProvider> so Clerk nonces
// its loader, then strict-dynamic becomes possible.)
//
// 'unsafe-eval': intentionally OMITTED. Next.js (App Router, production) and the
// modern Clerk SDK do not require eval at runtime; local dev (HMR, which does
// eval) bypasses CSP entirely below. If a violation surfaces a genuine eval
// need, set CSP_ALLOW_UNSAFE_EVAL = true to re-admit it.
//
// Edge-safe: btoa + Web Crypto only, no node:* (see feedback_middleware_edge_runtime).
const CSP_ALLOW_UNSAFE_EVAL = false;
const CLERK_HOSTS =
  "https://clerk.radon.run https://*.clerk.accounts.dev https://clerk.accounts.dev";

// Cloudflare Turnstile CAPTCHA — Clerk loads its bot-protection challenge from
// this origin as both an unnonced <script src> and a sandboxed <iframe>.
// Must appear in script-src AND frame-src; connect-src / img-src / worker-src
// are already open via their https: / wss: / blob: wildcards.
const CAPTCHA_HOSTS = "https://challenges.cloudflare.com";

export function generateNonce(): string {
  return btoa(crypto.randomUUID());
}

export function buildCspWithNonce(nonce: string): string {
  const scriptSrc = [
    "script-src 'self'",
    `'nonce-${nonce}'`,
    CLERK_HOSTS,
    CAPTCHA_HOSTS,
    CSP_ALLOW_UNSAFE_EVAL ? "'unsafe-eval'" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return [
    "default-src 'self'",
    scriptSrc,
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' https: data: blob:",
    "connect-src 'self' wss: https:",
    // Clerk's clerk-js spawns a same-origin blob: Web Worker (bot/telemetry);
    // worker-src falls back to script-src, which would block it without this.
    "worker-src 'self' blob:",
    `frame-src 'self' ${CLERK_HOSTS} ${CAPTCHA_HOSTS}`,
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    `form-action 'self' ${CLERK_HOSTS}`,
  ].join("; ");
}

// Build the pass-through response for an HTML route: forward the nonce to the
// app (so ThemeBootstrap's inline <script nonce> and Next.js's own framework
// scripts pick it up) AND set the matching enforced CSP on the response. The
// nonce header and the CSP header ALWAYS travel together; any path that does
// not call this emits no CSP, so it can never white-screen for a missing nonce.
export function withNonceCsp(request: NextRequest): NextResponse {
  const nonce = generateNonce();
  const csp = buildCspWithNonce(nonce);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  // Next.js reads the nonce from the CSP request header to nonce its own
  // framework <script> tags; x-nonce is what our own components read.
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

// Explicit test-flag override (used by Playwright via RADON_AUTHLESS_TEST=1).
// Kept for parity with FastAPI's own bypass and to allow CI-driven authless runs.
export function isLocalAuthlessTestBypassEnabled(url: URL, flag = process.env.RADON_AUTHLESS_TEST): boolean {
  if (flag !== "1") return false;
  return isLocalHost(url);
}

// Local-dev auto-bypass: any time `next dev` runs against localhost we skip
// Clerk so the developer never sees the sign-in wall. Production builds set
// NODE_ENV=production so this is a no-op there even if someone reverse-proxies
// localhost. The FastAPI side already auto-skips for 127.0.0.1/::1 callers
// (see scripts/api/auth.py).
export function isLocalDevAuthBypassEnabled(
  url: URL,
  nodeEnv = process.env.NODE_ENV,
): boolean {
  if (nodeEnv === "production") return false;
  return isLocalHost(url);
}

// Share-card link previews — link-preview bots (Twitter, Slack, iMessage)
// have no Clerk session and can't sign in. EXPLICIT list, not a pattern:
// the old `/^\/api(?:\/[^/]+)*\/share(?:\/.*)?$/` regex silently published
// any future `/api/**/share*` path the moment its route file shipped. A new
// share route must be added here deliberately (and to the filesystem pin in
// web/tests/middleware-share-allowlist.test.ts, which fails until it is).
export const PUBLIC_SHARE_API_ROUTES = [
  "/api/gex/share",
  "/api/gex/share/content",
  "/api/internals/share",
  "/api/internals/share/content",
  "/api/menthorq/cta/share",
  "/api/menthorq/cta/share/content",
  "/api/regime/share",
  "/api/regime/share/content",
  "/api/share/pnl",
  "/api/vcg/share",
  "/api/vcg/share/content",
] as const;

// Inbound webhooks — verified by the SENDER's signature inside the route
// handler (svix HMAC for Clerk), not by a Clerk session. EXPLICIT list with a
// filesystem pin (web/tests/middleware-share-allowlist.test.ts), same
// default-deny discipline as the share + probe scopes: a new webhook route
// must be added here deliberately, never auto-published by a pattern.
export const PUBLIC_WEBHOOK_API_ROUTES = ["/api/webhooks/clerk"] as const;

// Public allowlist. Every other route — pages AND /api/* — requires a Clerk
// session. The narrow exemptions:
//
//   /sign-in, /sign-up                  — Clerk auth flow pages
//   PUBLIC_SHARE_API_ROUTES             — share-card link previews (above)
//   /api/service-health                 — dashboard banner data; intentionally
//                                          accessible so monitoring pollers
//                                          and the future public status page
//                                          don't need a session.
//   /api/health                         — pre-approved liveness probe for any
//                                          future Next.js-side health route.
//
// Before 2026-05-15 the matcher contained `/api/(.*)` which left every API
// route open to the world. The page route protection was always working;
// only `/api/*` was the hole.
export const isPublicRoute = createRouteMatcher([
  "/sign-in(.*)",
  "/sign-up(.*)",
  ...PUBLIC_SHARE_API_ROUTES,
  ...PUBLIC_WEBHOOK_API_ROUTES,
  "/api/service-health",
  "/api/health",
]);

export function isApiPath(pathname: string): boolean {
  return pathname === "/api" || pathname.startsWith("/api/");
}

// Operator allowlist — the AUTHORIZATION layer on top of authentication.
//
// A valid Clerk session authenticates a user; it does NOT authorize them. The
// data tables (portfolio/journal/orders) are global with no user_id, and the
// FastAPI ALLOWED_USER_IDS allowlist is bypassed for trusted-local Next->API
// calls — so without this gate ANY signed-in user in the production Clerk
// instance could read the operator's real account (incident 2026-06-27).
//
// When ALLOWED_USER_IDS is set, only those Clerk user ids may proceed; every
// other authenticated user gets 403 — including demo-role users, who belong on
// demo.radon.run, not the operator app. Empty/unset => no enforcement, so local
// dev, CI/tests, and the demo deployment (where ALLOWED_USER_IDS is absent and
// the demo gate governs instead) are unaffected.
//
// Edge-safe: pure string ops, no node:* imports (the middleware runs in the
// Edge runtime; see feedback_middleware_edge_runtime).
export function parseAllowedUserIds(
  raw: string | undefined = process.env.ALLOWED_USER_IDS,
): Set<string> {
  return new Set(
    (raw ?? "")
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean),
  );
}

export function isAuthorizedUser(
  userId: string,
  raw: string | undefined = process.env.ALLOWED_USER_IDS,
): boolean {
  const allow = parseAllowedUserIds(raw);
  if (allow.size === 0) return true; // no allowlist configured -> don't enforce
  return allow.has(userId);
}

// Bearer-gated probe surface (DUR-16) — the Tier-3 off-box prober (GitHub
// Actions, no Clerk session) authenticates with
// `Authorization: Bearer ${RADON_PROBE_FRESHNESS_TOKEN}` instead of Clerk.
// DELIBERATELY not in isPublicRoute: a public listing would skip the bearer
// check entirely. EXPLICIT list, same default-deny discipline as
// PUBLIC_SHARE_API_ROUTES — a new probe route must be added here AND to the
// filesystem pin in web/tests/middleware-share-allowlist.test.ts.
export const PROBE_BEARER_API_ROUTES = ["/api/probe/freshness"] as const;

export function isProbeBearerRoute(pathname: string): boolean {
  return (PROBE_BEARER_API_ROUTES as readonly string[]).includes(pathname);
}

/**
 * Middleware gate for the probe routes. Returns:
 *   - null                  — not a probe route; fall through to Clerk.
 *   - NextResponse.next()   — correct bearer token; let the route run.
 *   - 401 JSON              — missing/wrong token, or the server token is
 *                             unset (fail closed). Body carries no detail
 *                             about WHY, so the response doesn't help an
 *                             attacker distinguish the cases.
 *
 * Token compare is timing-safe via Web Crypto (lib/probeAuth.ts) — the
 * middleware runs in the Edge runtime, so node:crypto is off the table.
 */
export async function handleProbeBearerGate(
  request: NextRequest,
  expectedToken: string | undefined = process.env.RADON_PROBE_FRESHNESS_TOKEN,
): Promise<NextResponse | null> {
  if (!isProbeBearerRoute(request.nextUrl.pathname)) return null;
  const authorized = await isAuthorizedProbeRequest(
    request.headers.get("authorization"),
    expectedToken,
  );
  if (authorized) return NextResponse.next();
  const requestId = getRequestId();
  const response = jsonApiError({
    message: "Unauthorized",
    status: 401,
    code: "UNAUTHORIZED",
    requestId,
  });
  return setNoStoreResponseHeaders(response, requestId);
}

export default clerkMiddleware(async (auth, request) => {
  // Probe routes are bearer-gated EVERYWHERE — before the local-dev bypass —
  // so the gate behaves identically in dev, tests, and production.
  const probeGate = await handleProbeBearerGate(request);
  if (probeGate) return probeGate;

  if (
    isLocalDevAuthBypassEnabled(request.nextUrl) ||
    isLocalAuthlessTestBypassEnabled(request.nextUrl)
  ) {
    return NextResponse.next();
  }

  // Public HTML auth pages (/sign-in, /sign-up) still render through the root
  // layout + ThemeBootstrap, so they need the nonce + enforced CSP too. The
  // enumerated public *API* routes (share/webhook) return JSON with no inline
  // scripts — emitting CSP on them is harmless and keeps one code path.
  if (isPublicRoute(request)) return withNonceCsp(request);

  const isApi = isApiPath(request.nextUrl.pathname);
  const { userId, sessionClaims } = await auth();

  // 1) Authentication — must have a Clerk session.
  //
  // API routes return a JSON 401 with the same shape as every other API error
  // response (see web/lib/apiContracts.ts). Clerk's default for a protected
  // route is to redirect to /sign-in, which is meaningless for an API client
  // and would also surface as a noisy 302 in the browser console when the
  // cookie expires mid-session. Page routes keep the standard redirect.
  if (!userId) {
    if (isApi) {
      const requestId = getRequestId();
      const response = jsonApiError({
        message: "Unauthorized",
        status: 401,
        code: "UNAUTHORIZED",
        requestId,
      });
      return setNoStoreResponseHeaders(response, requestId);
    }
    await auth.protect();
    return;
  }

  // 2) Authorization — a Clerk session is necessary but NOT sufficient. When
  // ALLOWED_USER_IDS is configured (production app.radon.run), only the
  // operator id(s) pass; every other authenticated user is forbidden (403),
  // including demo-role users (they belong on demo.radon.run). No allowlist =>
  // no enforcement, so the demo gate below governs the demo deployment.
  if (!isAuthorizedUser(userId)) {
    const requestId = getRequestId();
    if (isApi) {
      const response = jsonApiError({
        message: "Forbidden",
        status: 403,
        code: "FORBIDDEN",
        requestId,
      });
      return setNoStoreResponseHeaders(response, requestId);
    }
    const response = new NextResponse(
      "Not authorized. This account does not have access to Radon.",
      { status: 403, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
    return setNoStoreResponseHeaders(response, requestId);
  }

  // 3) Demo trial gate (expiry + tiered rate-limit). No-ops for non-demo users.
  const demoGate = await handleDemoGate({
    userId,
    metadata: sessionClaims?.metadata as DemoPublicMetadata | undefined,
    request,
  });
  if (demoGate) return demoGate;

  // Authenticated + authorized: render the page with a fresh nonce + enforced
  // CSP. This is the former implicit `return;` (NextResponse.next) fall-through.
  return withNonceCsp(request);
});

export const config = {
  matcher: [
    // Skip Next.js internals and all static files, unless found in search params
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes
    "/(api|trpc)(.*)",
  ],
};
